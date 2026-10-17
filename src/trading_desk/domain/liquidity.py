"""How liquid an option chain is, and which underlyings earn a place in the universe.

The metric is **premium turnover**: last traded price x volume, summed over every
leg of the chain. Three alternatives were rejected:

- *Equity turnover* measures the wrong market. A share can trade heavily while its
  option chain is untradeable, which is the trap this whole step exists to avoid.
- *Notional turnover* (strike x lot size x volume) is dominated by high-strike names
  and says nothing about whether a fill is available.
- *Open interest alone* is a stock, not a flow. A large OI built up months ago can
  sit in a chain nobody trades today.

So turnover ranks, and open interest and priced-leg coverage act as floors. The
third floor matters most and is the least obvious: a chain can report a respectable
turnover from a handful of near-the-money strikes while most legs have never traded.
Those chains quote a spread you cannot cross, so a minimum fraction of *priced* legs
is required alongside the turnover figure.

Pure module: everything here takes its data as arguments and reads no clock.
"""

from __future__ import annotations

from dataclasses import dataclass

from trading_desk.domain.market import OptionChainSnapshot
from trading_desk.domain.values import Money, Quantity


@dataclass(frozen=True)
class LiquidityMeasure:
    """What one chain measured. Every field is a fact, not a verdict.

    Legs rather than strikes, because a strike with only a call listed is half a
    strike, and counting it as whole overstates coverage.

    Deliberately carries no timestamp. The chain's `exchange_time` looked like the
    obvious provenance field and is not: Dhan stamps it with the time it answered
    the request, not the time the figures describe. A survey run at midnight on a
    Saturday reports an exchange_time of midnight on Saturday while serving
    Friday's volume. Which session a measurement describes is therefore decided
    from the trading calendar by the caller, never read back from the feed.
    """

    symbol: str
    is_index: bool
    premium_turnover: Money
    open_interest: int
    legs_priced: int
    legs_total: int

    @property
    def priced_fraction(self) -> float:
        """Share of legs with an implied volatility, so with a usable price."""
        if self.legs_total == 0:
            return 0.0
        return self.legs_priced / self.legs_total


@dataclass(frozen=True)
class LiquidityRules:
    """Thresholds, loaded from config/exchange_rules.yaml rather than written here.

    CLAUDE.md rule 7: exchange-shaped numbers are config with an effective-from
    date, because they change by circular and a literal in code has no history.
    """

    min_premium_turnover: Money
    min_open_interest: int
    min_priced_fraction: float
    max_stocks: int


@dataclass(frozen=True)
class Exclusion:
    """Why one symbol did not make the universe.

    `reason` carries the measured value *and* the threshold it was compared
    against (CLAUDE.md rule 9). "ADANIPOWER excluded" is unactionable; "turnover
    ₹2.1L below the ₹50L floor" tells you whether the floor or the name is wrong.
    """

    symbol: str
    reason: str


@dataclass(frozen=True)
class LiquidityRanking:
    """The ranked universe plus every name that was dropped, and why."""

    included: tuple[LiquidityMeasure, ...]
    excluded: tuple[Exclusion, ...]

    @property
    def symbols(self) -> tuple[str, ...]:
        return tuple(measure.symbol for measure in self.included)


def measure_chain(snapshot: OptionChainSnapshot, is_index: bool) -> LiquidityMeasure:
    """Reduce a full chain to the handful of numbers the ranking compares.

    A leg with zero volume contributes zero turnover rather than being skipped, so
    `legs_total` stays an honest denominator for priced_fraction.
    """
    turnover = Money.zero()
    open_interest = 0
    legs_priced = 0
    legs_total = 0

    for strike in snapshot.strikes:
        for quote in (strike.call, strike.put):
            if quote is None:
                continue
            legs_total += 1
            turnover = turnover + quote.last.times(Quantity(quote.volume))
            open_interest += quote.open_interest
            if quote.implied_volatility is not None:
                legs_priced += 1

    return LiquidityMeasure(
        symbol=snapshot.underlying.symbol,
        is_index=is_index,
        premium_turnover=turnover,
        open_interest=open_interest,
        legs_priced=legs_priced,
        legs_total=legs_total,
    )


def _why_excluded(measure: LiquidityMeasure, rules: LiquidityRules) -> str | None:
    """The first floor this measure falls below, or None if it clears all three."""
    if measure.premium_turnover.paise < rules.min_premium_turnover.paise:
        return (
            f"premium turnover {measure.premium_turnover} below the "
            f"{rules.min_premium_turnover} floor"
        )
    if measure.open_interest < rules.min_open_interest:
        return (
            f"open interest {measure.open_interest:,} below the {rules.min_open_interest:,} floor"
        )
    if measure.priced_fraction < rules.min_priced_fraction:
        return (
            f"only {measure.priced_fraction:.0%} of {measure.legs_total} legs priced, "
            f"below the {rules.min_priced_fraction:.0%} floor"
        )
    return None


def rank_by_liquidity(
    measures: tuple[LiquidityMeasure, ...], rules: LiquidityRules
) -> LiquidityRanking:
    """Rank by premium turnover, then apply the floors and the stock cap.

    Indices are not capped and not given a free pass. They are listed separately
    because there are only a few of them and ranking them against 200 stocks would
    let the cap push BANKNIFTY out; they still have to clear every floor. If NIFTY
    ever fails one, that is a fact worth seeing rather than hiding behind an
    always-include list.
    """
    by_turnover = sorted(measures, key=lambda m: m.premium_turnover.paise, reverse=True)

    passed = []
    excluded = []
    for measure in by_turnover:
        reason = _why_excluded(measure, rules)
        if reason is None:
            passed.append(measure)
        else:
            excluded.append(Exclusion(measure.symbol, reason))

    indices = [measure for measure in passed if measure.is_index]
    stocks = [measure for measure in passed if not measure.is_index]

    for measure in stocks[rules.max_stocks :]:
        excluded.append(
            Exclusion(
                measure.symbol,
                f"ranked below the top {rules.max_stocks} stocks by premium turnover "
                f"(turnover {measure.premium_turnover})",
            )
        )

    return LiquidityRanking(
        included=tuple(indices + stocks[: rules.max_stocks]),
        excluded=tuple(excluded),
    )
