"""Setup B - Index Volatility-Regime Spread. One setup, two variants, a gap.

| IV rank | Variant | Structure |
|---|---|---|
| >= 70 | B1 | credit spread, short delta 0.15-0.20, 4-9 DTE |
| 35-70 | **no trade** | a valid and frequent output |
| <= 35 | B2 | debit vertical in the regime direction, 10-20 DTE |

**The middle band is the most common answer and the easiest thing to break.**
docs/06 calls it "a valid and frequent output", and it is tested explicitly
rather than left as the fall-through case, because a refactor that turns
no-trade into a trade is silent and expensive.

Note the bands do not tile the number line: `>= 70` and `<= 35` leave
35 < rank < 70 as no-trade, and **35 and 70 themselves are tradable**. That
asymmetry is from docs/06 and is preserved deliberately - it is the kind of
boundary that gets "tidied" into a different rulebook by accident.

**Naked shorts are unreachable here, not merely switched off** (FR-5.9). The
credit variant returns a two-leg structure with a protective wing or nothing;
there is no code path that emits a lone short, and `allow_naked_shorts` is
checked anyway so that flipping the config to true still cannot produce one.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from trading_desk.domain.gates import GateReport, GateResult, failed, no_data, passed
from trading_desk.domain.setups.thresholds import SetupBThresholds


class IndexVariant(StrEnum):
    """Which side of the volatility regime we are on, if either."""

    CREDIT = "B1_CREDIT"  # sell elevated vol
    DEBIT = "B2_DEBIT"  # buy cheap vol with trend
    NO_TRADE = "NO_TRADE"  # the middle band, and the usual answer
    UNKNOWN = "UNKNOWN"  # no IV rank - distinct from no-trade


@dataclass(frozen=True)
class RegimeDecision:
    """The variant and why, carrying the number it was decided on (rule 9)."""

    variant: IndexVariant
    iv_rank: float | None
    reason: str

    @property
    def is_tradable(self) -> bool:
        return self.variant in (IndexVariant.CREDIT, IndexVariant.DEBIT)


@dataclass(frozen=True)
class SetupBCandidate:
    instrument_symbol: str
    iv_rank: float | None = None
    iv_rank_is_proxy: bool = False
    # B2 needs a direction to express; B1 does not.
    is_trending: bool = False
    days_to_expiry: int | None = None
    sessions_to_expiry: int | None = None
    events_in_window: tuple[str, ...] = ()
    open_index_positions: int = 0


def classify_regime(candidate: SetupBCandidate, limits: SetupBThresholds) -> RegimeDecision:
    """Pick the variant from IV rank. The middle band is a real answer.

    UNKNOWN is separate from NO_TRADE: "we have no IV rank" and "IV rank says
    sit out" are different facts, and only the first one gets fixed by waiting
    for history to accrue.
    """
    rank = candidate.iv_rank
    if rank is None:
        return RegimeDecision(IndexVariant.UNKNOWN, None, "no IV rank available")

    source = "proxy" if candidate.iv_rank_is_proxy else "actual"
    if rank >= limits.credit_regime_min_iv_rank:
        return RegimeDecision(
            IndexVariant.CREDIT,
            rank,
            f"IV rank {rank:.1f} ({source}) >= {limits.credit_regime_min_iv_rank}",
        )
    if rank <= limits.debit_regime_max_iv_rank:
        if not candidate.is_trending:
            return RegimeDecision(
                IndexVariant.NO_TRADE,
                rank,
                f"IV rank {rank:.1f} ({source}) is low but the index is not trending",
            )
        return RegimeDecision(
            IndexVariant.DEBIT,
            rank,
            f"IV rank {rank:.1f} ({source}) <= {limits.debit_regime_max_iv_rank} and trending",
        )
    return RegimeDecision(
        IndexVariant.NO_TRADE,
        rank,
        f"IV rank {rank:.1f} ({source}) is in the "
        f"{limits.debit_regime_max_iv_rank}-{limits.credit_regime_min_iv_rank} no-trade band",
    )


def regime_is_tradable(candidate: SetupBCandidate, limits: SetupBThresholds) -> GateResult:
    name = "regime"
    threshold = (
        f">= {limits.credit_regime_min_iv_rank} (credit) or "
        f"<= {limits.debit_regime_max_iv_rank} with trend (debit)"
    )
    decision = classify_regime(candidate, limits)
    if decision.variant is IndexVariant.UNKNOWN:
        return no_data(name, threshold, decision.reason)
    if decision.is_tradable:
        return passed(name, decision.variant, threshold, decision.reason)
    return failed(name, decision.variant, threshold, decision.reason)


def expiry_window(candidate: SetupBCandidate, limits: SetupBThresholds) -> GateResult:
    """The window depends on the variant, so the variant is decided first."""
    name = "expiry_window"
    decision = classify_regime(candidate, limits)
    window = (
        limits.credit_expiry if decision.variant is IndexVariant.CREDIT else limits.debit_expiry
    )
    threshold = f"{window.min_days}-{window.max_days} DTE for {decision.variant}"

    if candidate.days_to_expiry is None:
        return no_data(name, threshold, "no expiry selected")

    measured = f"{candidate.days_to_expiry} DTE"
    if window.min_days <= candidate.days_to_expiry <= window.max_days:
        return passed(name, measured, threshold)
    return failed(name, measured, threshold, "outside the variant's window")


def flat_before_expiry(candidate: SetupBCandidate, limits: SetupBThresholds) -> GateResult:
    """No position held into expiry day. No exceptions.

    docs/06: "expiry-day gamma is how defined-risk positions stop being
    defined-risk in practice". This is a hard gate, not a preference.
    """
    name = "flat_before_expiry"
    threshold = f"> {limits.flat_by_sessions_before_expiry} session(s) to expiry at entry"
    if candidate.sessions_to_expiry is None:
        return no_data(name, threshold, "sessions to expiry unknown")

    measured = f"{candidate.sessions_to_expiry} session(s)"
    if candidate.sessions_to_expiry > limits.flat_by_sessions_before_expiry:
        return passed(name, measured, threshold)
    return failed(name, measured, threshold, "too close to expiry to open")


def no_blocking_event(candidate: SetupBCandidate, limits: SetupBThresholds) -> GateResult:
    """RBI policy, CPI, IIP, FOMC or the Budget inside the window blocks entry."""
    name = "macro_calendar"
    threshold = f"none of {sorted(limits.blocking_event_types)} inside the window"
    blocking = sorted(
        {event.upper() for event in candidate.events_in_window} & limits.blocking_event_types
    )
    if blocking:
        return failed(name, ", ".join(blocking), threshold, "a macro event falls in the window")
    return passed(name, "none", threshold)


def one_index_position(candidate: SetupBCandidate, limits: SetupBThresholds) -> GateResult:
    name = "position_limit"
    threshold = f"< {limits.max_concurrent_index_positions} open index position(s)"
    measured = f"{candidate.open_index_positions} open"
    if candidate.open_index_positions < limits.max_concurrent_index_positions:
        return passed(name, measured, threshold)
    return failed(name, measured, threshold, "one index position at a time")


GATES = (
    regime_is_tradable,
    expiry_window,
    flat_before_expiry,
    no_blocking_event,
    one_index_position,
)


def evaluate(candidate: SetupBCandidate, limits: SetupBThresholds) -> GateReport:
    return GateReport(
        instrument_symbol=candidate.instrument_symbol,
        results=tuple(gate(candidate, limits) for gate in GATES),
    )
