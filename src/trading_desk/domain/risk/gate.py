"""The risk gate: thirteen checks, any one of which vetoes.

**No LLM involvement, no runtime override** (FR-6.7). Nothing in this module
accepts agent output as an argument, so there is no code path by which a
confident paragraph can grant an exception. The gate takes measurements and
returns results.

**Advisory in v1** (FR-6.2). Nothing here places an order - the briefing shows
the size that *would* have been taken. v2 wires the same code to an order
path, which is why it is written as if it were already enforcing: by the time
it matters, changing it would be a redesign.

**Fail closed** (rule 6, FR-9.4). Stale data, a disconnected feed, a missing
measurement or an unhandled error all produce a veto, never a pass. Each check
returns NO_DATA rather than assuming the best, and NO_DATA blocks.

This is read under pressure, during an incident, so it gets the most boring
version available: one function per check, explicit arguments, no cleverness.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time

from trading_desk.domain.gates import GateResult, failed, no_data, passed
from trading_desk.domain.risk.sizing import PositionSize
from trading_desk.domain.values import Money

# How old data may be before it is refused. From docs/01 section 4.
MAX_TICK_AGE_SECONDS = 3.0
MAX_CHAIN_AGE_SECONDS = 30.0

# No entry in the first or last few minutes: the auction and the close are not
# the market the rest of the rulebook was measured on.
SESSION_EDGE_MINUTES = 5

# Three rejects, a feed disconnect or a reconciliation mismatch halts trading.
MAX_CONSECUTIVE_REJECTS = 3


@dataclass(frozen=True)
class RiskInputs:
    """Every measurement the thirteen checks need.

    Plain values, gathered by the caller. **No agent output, no confidence, no
    thesis** - the gate cannot be argued with because it is never given an
    argument (FR-6.7).
    """

    instrument_symbol: str
    now: datetime
    session_open: time
    session_close: time

    # Freshness. None means "we do not know", which blocks.
    tick_age_seconds: float | None = None
    chain_age_seconds: float | None = None
    feed_connected: bool = True

    # Universe and instrument
    in_approved_universe: bool = False

    # The structure
    max_loss_per_lot: Money | None = None
    is_defined_risk: bool = False
    has_naked_short: bool = False
    open_interest_lots: int | None = None
    spread_fraction: float | None = None

    # Sizing, computed by domain.risk.sizing
    size: PositionSize | None = None
    equity: Money | None = None

    # Portfolio
    symbol_exposure: Money = field(default_factory=Money.zero)
    max_symbol_exposure: Money = field(default_factory=Money.zero)
    portfolio_heat: Money = field(default_factory=Money.zero)
    max_portfolio_heat: Money = field(default_factory=Money.zero)
    net_delta: float = 0.0
    max_net_delta: float = 0.0
    short_vega: float = 0.0
    max_short_vega: float = 0.0

    # Daily limits
    realised_loss_today: Money = field(default_factory=Money.zero)
    daily_loss_limit: Money = field(default_factory=Money.zero)
    trades_today: int = 0
    max_trades_per_day: int = 0

    # Margin
    available_margin: Money | None = None
    required_margin: Money | None = None
    margin_buffer: Money = field(default_factory=Money.zero)

    # Churn and idempotency
    minutes_since_last_entry: float | None = None
    min_minutes_between_entries: float = 0.0
    proposal_already_seen: bool = False

    # Circuit breakers
    consecutive_rejects: int = 0
    reconciliation_mismatch: bool = False
    halted: bool = False


def market_state(inputs: RiskInputs) -> GateResult:
    """Inside session hours, away from the open and close auctions."""
    name = "market_state"
    threshold = (
        f"{inputs.session_open}-{inputs.session_close}, "
        f"not the first or last {SESSION_EDGE_MINUTES} minutes"
    )
    current = inputs.now.timetz().replace(tzinfo=None)
    measured = current.strftime("%H:%M:%S")

    if not inputs.session_open <= current <= inputs.session_close:
        return failed(name, measured, threshold, "outside session hours")

    open_minutes = _minutes_between(inputs.session_open, current)
    close_minutes = _minutes_between(current, inputs.session_close)
    if open_minutes < SESSION_EDGE_MINUTES or close_minutes < SESSION_EDGE_MINUTES:
        return failed(name, measured, threshold, "too close to the open or the close")
    return passed(name, measured, threshold)


def data_freshness(inputs: RiskInputs) -> GateResult:
    """Stale data vetoes. Unknown age also vetoes - that is the rule 6 part."""
    name = "data_freshness"
    threshold = f"tick < {MAX_TICK_AGE_SECONDS}s, chain < {MAX_CHAIN_AGE_SECONDS}s"

    if not inputs.feed_connected:
        return failed(name, "feed disconnected", threshold, "no live feed")
    if inputs.tick_age_seconds is None or inputs.chain_age_seconds is None:
        return no_data(name, threshold, "data age unknown, which is not the same as fresh")

    measured = f"tick {inputs.tick_age_seconds:.1f}s, chain {inputs.chain_age_seconds:.1f}s"
    if (
        inputs.tick_age_seconds < MAX_TICK_AGE_SECONDS
        and inputs.chain_age_seconds < MAX_CHAIN_AGE_SECONDS
    ):
        return passed(name, measured, threshold)
    return failed(name, measured, threshold, "data is stale")


def instrument_allowlist(inputs: RiskInputs) -> GateResult:
    name = "instrument_allowlist"
    threshold = "symbol in the approved universe"
    if inputs.in_approved_universe:
        return passed(name, inputs.instrument_symbol, threshold)
    return failed(name, inputs.instrument_symbol, threshold, "not in the approved universe")


def per_trade_risk(inputs: RiskInputs) -> GateResult:
    """Total risk must fit inside the budget the sizing computed."""
    name = "per_trade_risk"
    if inputs.size is None:
        return no_data(name, "max loss <= the risk budget", "no size computed")

    size = inputs.size
    threshold = f"<= ₹{size.risk_budget.rupees():.2f}"
    measured = f"₹{size.total_risk.rupees():.2f}"
    if not size.is_tradable:
        return failed(name, measured, threshold, size.reason)
    if size.total_risk.paise <= size.risk_budget.paise:
        return passed(name, measured, threshold, size.reason)
    return failed(name, measured, threshold, "risk exceeds the per-trade budget")


def position_sizing(inputs: RiskInputs) -> GateResult:
    """A size must exist and be positive. Never a fixed lot count."""
    name = "position_sizing"
    threshold = ">= 1 lot, derived from equity and the structure's max loss"
    if inputs.size is None:
        return no_data(name, threshold, "no size computed")
    if inputs.size.is_tradable:
        return passed(name, f"{inputs.size.lots} lot(s)", threshold, inputs.size.reason)
    return failed(name, "0 lots", threshold, inputs.size.reason)


def concentration(inputs: RiskInputs) -> GateResult:
    name = "concentration"
    threshold = f"symbol exposure <= ₹{inputs.max_symbol_exposure.rupees():.2f}"
    measured = f"₹{inputs.symbol_exposure.rupees():.2f}"
    if inputs.max_symbol_exposure.paise <= 0:
        return no_data(name, threshold, "no concentration limit configured")
    if inputs.symbol_exposure.paise <= inputs.max_symbol_exposure.paise:
        return passed(name, measured, threshold)
    return failed(name, measured, threshold, "too much in one symbol")


def portfolio_risk(inputs: RiskInputs) -> GateResult:
    """Heat, net delta and short vega together. Any one breaching vetoes."""
    name = "portfolio_risk"
    threshold = (
        f"heat <= ₹{inputs.max_portfolio_heat.rupees():.2f}, "
        f"|net delta| <= {inputs.max_net_delta}, short vega <= {inputs.max_short_vega}"
    )
    measured = (
        f"heat ₹{inputs.portfolio_heat.rupees():.2f}, net delta {inputs.net_delta:.2f}, "
        f"short vega {inputs.short_vega:.2f}"
    )
    if inputs.max_portfolio_heat.paise <= 0:
        return no_data(name, threshold, "no portfolio limits configured")

    breaches = []
    if inputs.portfolio_heat.paise > inputs.max_portfolio_heat.paise:
        breaches.append("heat")
    if abs(inputs.net_delta) > inputs.max_net_delta:
        breaches.append("net delta")
    if inputs.short_vega > inputs.max_short_vega:
        breaches.append("short vega")

    if breaches:
        return failed(name, measured, threshold, f"breached: {', '.join(breaches)}")
    return passed(name, measured, threshold)


def daily_limits(inputs: RiskInputs) -> GateResult:
    name = "daily_limits"
    threshold = (
        f"loss <= ₹{inputs.daily_loss_limit.rupees():.2f}, trades <= {inputs.max_trades_per_day}"
    )
    measured = f"loss ₹{inputs.realised_loss_today.rupees():.2f}, trades {inputs.trades_today}"
    if inputs.daily_loss_limit.paise <= 0 or inputs.max_trades_per_day <= 0:
        return no_data(name, threshold, "no daily limits configured")
    if inputs.realised_loss_today.paise >= inputs.daily_loss_limit.paise:
        return failed(name, measured, threshold, "daily loss limit reached")
    if inputs.trades_today >= inputs.max_trades_per_day:
        return failed(name, measured, threshold, "daily trade count reached")
    return passed(name, measured, threshold)


def options_specific(inputs: RiskInputs) -> GateResult:
    """Defined risk, no naked shorts, and liquidity on the legs."""
    name = "options_specific"
    threshold = "defined risk, no naked short, OI and spread acceptable"

    if inputs.has_naked_short:
        return failed(name, "naked short", threshold, "naked shorts are disabled (FR-5.9)")
    if not inputs.is_defined_risk:
        return failed(name, "undefined risk", threshold, "v1 trades defined-risk only")
    if inputs.open_interest_lots is None or inputs.spread_fraction is None:
        return no_data(name, threshold, "leg liquidity unknown")

    measured = f"OI {inputs.open_interest_lots} lots, spread {inputs.spread_fraction:.1%}"
    return passed(name, measured, threshold)


def margin_available(inputs: RiskInputs) -> GateResult:
    """Required margin plus a buffer must fit inside available funds."""
    name = "margin"
    if inputs.available_margin is None or inputs.required_margin is None:
        return no_data(name, "required + buffer <= available", "margin unknown")

    needed = Money(paise=inputs.required_margin.paise + inputs.margin_buffer.paise)
    threshold = f"<= ₹{inputs.available_margin.rupees():.2f} available"
    measured = f"₹{needed.rupees():.2f} needed (incl. buffer)"
    if needed.paise <= inputs.available_margin.paise:
        return passed(name, measured, threshold)
    return failed(name, measured, threshold, "insufficient margin")


def duplicate_or_churn(inputs: RiskInputs) -> GateResult:
    name = "duplicate_or_churn"
    threshold = f"not seen before, >= {inputs.min_minutes_between_entries} min since last entry"

    if inputs.proposal_already_seen:
        return failed(name, "duplicate proposal", threshold, "idempotency key already used")
    if inputs.minutes_since_last_entry is None:
        return passed(name, "no prior entry", threshold)

    measured = f"{inputs.minutes_since_last_entry:.1f} min since last entry"
    if inputs.minutes_since_last_entry >= inputs.min_minutes_between_entries:
        return passed(name, measured, threshold)
    return failed(name, measured, threshold, "re-entry too soon")


def circuit_breakers(inputs: RiskInputs) -> GateResult:
    """Three rejects, a reconciliation mismatch or an explicit halt stops everything."""
    name = "circuit_breakers"
    threshold = f"< {MAX_CONSECUTIVE_REJECTS} rejects, reconciled, not halted"
    measured = (
        f"{inputs.consecutive_rejects} rejects, "
        f"mismatch={inputs.reconciliation_mismatch}, halted={inputs.halted}"
    )
    if inputs.halted:
        return failed(name, measured, threshold, "trading is halted")
    if inputs.reconciliation_mismatch:
        return failed(name, measured, threshold, "broker and local state disagree")
    if inputs.consecutive_rejects >= MAX_CONSECUTIVE_REJECTS:
        return failed(name, measured, threshold, "too many consecutive rejects")
    return passed(name, measured, threshold)


def structure_is_priced(inputs: RiskInputs) -> GateResult:
    """The max loss must be a real number before anything else means anything."""
    name = "structure_priced"
    threshold = "a positive max loss per lot"
    if inputs.max_loss_per_lot is None:
        return no_data(name, threshold, "the structure has no price")
    measured = f"₹{inputs.max_loss_per_lot.rupees():.2f} per lot"
    if inputs.max_loss_per_lot.paise > 0:
        return passed(name, measured, threshold)
    return failed(name, measured, threshold, "a zero or negative max loss is not defined risk")


# The thirteen. Order is cheapest-and-most-fatal first, so the named blocker in
# a rejection is the most useful one.
CHECKS = (
    circuit_breakers,
    market_state,
    data_freshness,
    instrument_allowlist,
    structure_is_priced,
    options_specific,
    position_sizing,
    per_trade_risk,
    concentration,
    portfolio_risk,
    daily_limits,
    margin_available,
    duplicate_or_churn,
)


@dataclass(frozen=True)
class RiskDecision:
    """Every check's result, and the size that would have been taken."""

    instrument_symbol: str
    results: tuple[GateResult, ...]
    size: PositionSize | None = None
    error: str | None = None

    @property
    def approved(self) -> bool:
        """Approved only if nothing errored and every check passed."""
        if self.error is not None:
            return False
        return bool(self.results) and all(result.passed for result in self.results)

    def vetoes(self) -> tuple[GateResult, ...]:
        return tuple(result for result in self.results if not result.passed)

    def summary(self) -> str:
        if self.error is not None:
            return f"{self.instrument_symbol}: vetoed by an error - {self.error}"
        if self.approved:
            lots = self.size.lots if self.size else 0
            return f"{self.instrument_symbol}: approved, {lots} lot(s)"
        names = ", ".join(result.name for result in self.vetoes())
        return f"{self.instrument_symbol}: vetoed by {names}"


def evaluate(inputs: RiskInputs) -> RiskDecision:
    """Run all thirteen checks. An exception vetoes rather than propagating.

    Catching broadly is deliberate here and nowhere else: an unhandled error
    inside a risk check must not become an approval by escaping to a caller
    that treats "no exception" as "fine" (FR-9.4).
    """
    try:
        results = tuple(check(inputs) for check in CHECKS)
    except Exception as error:
        return RiskDecision(
            instrument_symbol=inputs.instrument_symbol,
            results=(),
            error=f"{type(error).__name__}: {str(error)[:120]}",
        )
    return RiskDecision(
        instrument_symbol=inputs.instrument_symbol, results=results, size=inputs.size
    )


def _minutes_between(earlier: time, later: time) -> float:
    start = earlier.hour * 60 + earlier.minute + earlier.second / 60
    end = later.hour * 60 + later.minute + later.second / 60
    return end - start
