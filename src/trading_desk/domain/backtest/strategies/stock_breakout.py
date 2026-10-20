"""Setup A's technical confirmation, as a backtestable strategy.

This is the rule ISS-039 measured: a close beyond the 20-day range edge on
1.5x the 20-day average volume. It is in here unchanged, and it calls the
**production gate** rather than a copy of it, so the dashboard's numbers
describe the rule the briefing actually uses.

**Its measured result is negative** - 37.0% win rate against a 46.3% breakeven
over 1,023 trades, and only 4 of 24 names net positive. It is kept as the
baseline every new strategy has to beat, not as a recommendation.
"""

from __future__ import annotations

from collections.abc import Sequence

from trading_desk.domain.backtest.strategy import ExitPolicy, Signal
from trading_desk.domain.enums import Direction
from trading_desk.domain.gates import GateResult, passed
from trading_desk.domain.indicators import (
    DEFAULT_RANGE_SESSIONS,
    DEFAULT_VOLUME_PERIOD,
    closed_beyond_range,
    volume_ratio,
)
from trading_desk.domain.market import Bar
from trading_desk.domain.setups.setup_a import SetupACandidate, technical_confirm
from trading_desk.domain.setups.thresholds import SetupAThresholds


class StockBreakout:
    """Range break plus volume, on a single stock."""

    def __init__(self, thresholds: SetupAThresholds) -> None:
        self._limits = thresholds

    @property
    def name(self) -> str:
        return "stock_breakout_volume"

    @property
    def trades_indices(self) -> bool:
        return False

    @property
    def exits(self) -> ExitPolicy:
        return ExitPolicy(stop_in_atr=1.0, target_in_atr=1.5, max_sessions_held=20)

    def describe(self) -> str:
        return (
            f"Setup A's technical_confirm: close beyond the {DEFAULT_RANGE_SESSIONS}-day "
            f"range edge AND volume >= {self._limits.min_volume_ratio}x the "
            f"{DEFAULT_VOLUME_PERIOD}-day average. The production gate, unmodified."
        )

    def signal_at(self, bars: Sequence[Bar], index: int) -> Signal | None:
        window = bars[: index + 1]
        direction = _break_direction(window)
        if direction is None:
            return None

        beyond = closed_beyond_range(window, DEFAULT_RANGE_SESSIONS)
        ratio = volume_ratio(window, DEFAULT_VOLUME_PERIOD)
        candidate = SetupACandidate(
            instrument_symbol=bars[index].instrument.symbol,
            direction=direction,
            close_beyond_range_edge=beyond,
            volume_ratio=ratio,
        )
        result = technical_confirm(candidate, self._limits)
        if not result.passed:
            return None
        return Signal(direction=direction, reasons=(result, _direction_reason(direction, window)))


def _break_direction(window: Sequence[Bar]) -> Direction | None:
    """Which edge the close broke, measured as the gate measures it."""
    if len(window) < DEFAULT_RANGE_SESSIONS + 1:
        return None
    prior = window[-(DEFAULT_RANGE_SESSIONS + 1) : -1]
    close = window[-1].close.paise
    if close > max(bar.high.paise for bar in prior):
        return Direction.BULLISH
    if close < min(bar.low.paise for bar in prior):
        return Direction.BEARISH
    return None


def _direction_reason(direction: Direction, window: Sequence[Bar]) -> GateResult:
    """The break itself, recorded as a measured value rather than a label."""
    prior = window[-(DEFAULT_RANGE_SESSIONS + 1) : -1]
    edge = (
        max(bar.high.paise for bar in prior)
        if direction is Direction.BULLISH
        else min(bar.low.paise for bar in prior)
    )
    close = window[-1].close.paise
    side = "high" if direction is Direction.BULLISH else "low"
    return passed(
        "range_break",
        f"close Rs {close / 100:,.2f} vs edge Rs {edge / 100:,.2f}",
        f"beyond the {DEFAULT_RANGE_SESSIONS}-day {side}",
    )
