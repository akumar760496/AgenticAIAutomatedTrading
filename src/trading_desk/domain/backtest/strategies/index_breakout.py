"""An index strategy, independent of the stock one by construction.

**Why it is a separate class rather than the stock rule with different
numbers.** ISS-039 measured the stock breakout at a 37% win rate and the *same
rule* applied to NIFTY and BANKNIFTY at 23%, on 39 trades in four years. An
index is an average of fifty names, so it rarely breaks a 20-day range on
unusual volume - the rule fires about ten times a year and is wrong three times
in four. Sharing parameters between the two was never defensible.

So this one asks a different question. An index trends rather than breaks, and
volume on an index is not the participation signal it is on a single stock
(index volume is a sum over constituents, dominated by the largest). What it
tests instead:

- the 20/50 moving-average regime, which is what `trend_regime` already
  computes for the technical agent, and
- a pullback *into* that trend rather than a breakout away from it: today's
  close in the lower part of its own recent range while the regime is up.

**None of this is backtested yet either.** It is a hypothesis written to be
measured, with a shorter horizon than the stock rule because an index mean-
reverts faster. Whatever the dashboard says about it is the first evidence it
has ever had.
"""

from __future__ import annotations

from collections.abc import Sequence

from trading_desk.domain.backtest.strategy import ExitPolicy, Signal
from trading_desk.domain.enums import Direction, TrendRegime
from trading_desk.domain.gates import GateResult, passed
from trading_desk.domain.indicators import price_range, trend_regime
from trading_desk.domain.market import Bar

# The window the pullback is measured in. Shorter than the stock rule's 20: an
# index pullback inside a trend resolves in days, not weeks.
PULLBACK_SESSIONS = 10

# How deep into its own recent range the close has to sit. 0.35 means the lower
# third for a long - a dip, not a collapse.
MAX_POSITION_IN_RANGE = 0.35

# Enough history for the slow moving average plus the pullback window.
MIN_BARS = 55


class IndexBreakout:
    """Pullback into an established index trend.

    The name is kept for continuity with the results already stored under it;
    it describes the family, not the mechanic.
    """

    @property
    def name(self) -> str:
        return "index_trend_pullback"

    @property
    def trades_indices(self) -> bool:
        return True

    @property
    def exits(self) -> ExitPolicy:
        """Tighter and shorter than the stock policy, deliberately.

        An index moves perhaps a third as much as a single stock in ATR terms
        relative to its own noise, and the 39-trade sample in ISS-039 showed
        30 of 39 stopping out - which is what a stop too wide for the horizon
        looks like from the other side.
        """
        return ExitPolicy(stop_in_atr=1.0, target_in_atr=2.0, max_sessions_held=10)

    def describe(self) -> str:
        return (
            f"Index pullback: a 20/50 MA trend is established, and the close sits "
            f"within {MAX_POSITION_IN_RANGE:.0%} of its {PULLBACK_SESSIONS}-session "
            f"range on the against-trend side. Never backtested before today."
        )

    def signal_at(self, bars: Sequence[Bar], index: int) -> Signal | None:
        window = bars[: index + 1]
        if len(window) < MIN_BARS:
            return None

        regime = trend_regime(window)
        if regime is TrendRegime.RANGING:
            return None

        position = _position_in_range(window)
        if position is None:
            return None

        direction = Direction.BULLISH if regime is TrendRegime.UPTREND else Direction.BEARISH
        # A long wants a dip (low in the range); a short wants a bounce (high in it).
        depth = position if direction is Direction.BULLISH else 1.0 - position
        if depth > MAX_POSITION_IN_RANGE:
            return None

        return Signal(
            direction=direction,
            reasons=(
                _regime_reason(regime),
                _pullback_reason(depth),
            ),
        )


def _position_in_range(window: Sequence[Bar]) -> float | None:
    """Where the latest close sits in the recent range, 0.0 low to 1.0 high.

    None when the range is zero - a close cannot have a position in a range
    that does not exist, and returning 0.5 would invent a measurement.
    """
    recent = window[-PULLBACK_SESSIONS:]
    span = price_range(recent)
    if span is None or span.paise <= 0:
        return None
    lowest = min(bar.low.paise for bar in recent)
    return (window[-1].close.paise - lowest) / span.paise


def _regime_reason(regime: TrendRegime) -> GateResult:
    return passed("index_regime", regime.value, "UPTREND or DOWNTREND, never RANGING")


def _pullback_reason(depth: float) -> GateResult:
    return passed(
        "pullback_depth",
        f"{depth:.0%} into the {PULLBACK_SESSIONS}-session range",
        f"<= {MAX_POSITION_IN_RANGE:.0%} against the trend",
    )
