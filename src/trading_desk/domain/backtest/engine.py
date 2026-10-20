"""Running one strategy over one instrument's bars.

Three rules here, and all three exist because the opposite choice makes a
backtest flatter itself:

1. **Entry is the open of the bar *after* the signal.** The signal is produced
   by a close; you cannot trade a price you needed in order to decide.
2. **A bar whose range contains both the stop and the target counts as a
   stop.** Daily bars cannot order the two, and assuming the favourable fill
   is the most common way a backtest lies.
3. **A gap through the stop fills at the open, not at the stop.** Honouring the
   stop level on a gap invents liquidity that was not there.

Pure: bars in, trades out. No clock, no I/O, no randomness.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from trading_desk.domain.backtest.strategy import ExitPolicy, Signal, Strategy
from trading_desk.domain.backtest.trade import (
    DEFAULT_COST_FRACTION,
    ExitReason,
    SimulatedTrade,
)
from trading_desk.domain.enums import Direction, TrendRegime
from trading_desk.domain.indicators import average_true_range
from trading_desk.domain.market import Bar
from trading_desk.domain.values import Money, Price


@dataclass(frozen=True)
class MarketContext:
    """What the wider market was doing, per trading date.

    Supplied by the caller rather than derived here: it comes from the index's
    own bars, and a strategy running on one stock has no business reading
    another instrument's series.
    """

    regime_by_date: dict[str, TrendRegime]

    def regime_on(self, trade_date: str) -> TrendRegime:
        return self.regime_by_date.get(trade_date, TrendRegime.RANGING)


def _levels(entry: int, atr: Money, direction: Direction, exits: ExitPolicy) -> tuple[int, int]:
    """Stop and target in paise, from the entry price."""
    stop_distance = int(exits.stop_in_atr * atr.paise)
    target_distance = int(exits.target_in_atr * atr.paise)
    if direction is Direction.BULLISH:
        return entry - stop_distance, entry + target_distance
    return entry + stop_distance, entry - target_distance


def _exit_on(
    bar: Bar, stop: int, target: int, direction: Direction
) -> tuple[int, ExitReason] | None:
    """The exit this bar produces, if any. Stop is tested first by design."""
    long = direction is Direction.BULLISH
    hit_stop = bar.low.paise <= stop if long else bar.high.paise >= stop
    if hit_stop:
        # A gap fills at the open; otherwise the stop level is reachable.
        gapped = bar.open.paise < stop if long else bar.open.paise > stop
        return (bar.open.paise if gapped else stop), ExitReason.STOP
    hit_target = bar.high.paise >= target if long else bar.low.paise <= target
    if hit_target:
        return target, ExitReason.TARGET
    return None


def simulate_one(
    bars: Sequence[Bar],
    signal_index: int,
    *,
    signal: Signal,
    strategy: Strategy,
    market: MarketContext,
    cost_fraction: float = DEFAULT_COST_FRACTION,
) -> SimulatedTrade | None:
    """One trade, or None when there is not enough series left to hold it."""
    atr = average_true_range(bars[: signal_index + 1])
    if atr is None or atr.paise <= 0 or signal_index + 1 >= len(bars):
        return None

    entry_bar = bars[signal_index + 1]
    entry = entry_bar.open.paise
    stop, target = _levels(entry, atr, signal.direction, strategy.exits)
    entry_date = str(entry_bar.start.date())
    regime = market.regime_on(entry_date)

    exit_paise, reason, held, exit_index = _walk_forward(
        bars,
        signal_index,
        stop=stop,
        target=target,
        direction=signal.direction,
        exits=strategy.exits,
    )
    if exit_index is None:
        return None

    return SimulatedTrade(
        strategy=strategy.name,
        symbol=entry_bar.instrument.symbol,
        is_index=strategy.trades_indices,
        direction=signal.direction,
        entry_date=entry_date,
        entry_price=Price(entry),
        exit_date=str(bars[exit_index].start.date()),
        exit_price=Price(exit_paise),
        sessions_held=held,
        exit_reason=reason,
        stop_level=Price(stop),
        target_level=Price(target),
        atr_at_entry=atr,
        reasons=signal.reasons,
        market_direction=regime,
        against_market=_is_against(signal.direction, regime),
        cost_fraction=cost_fraction,
    )


def _walk_forward(
    bars: Sequence[Bar],
    signal_index: int,
    *,
    stop: int,
    target: int,
    direction: Direction,
    exits: ExitPolicy,
) -> tuple[int, ExitReason, int, int | None]:
    """Hold the trade forward until a stop, a target or the time stop."""
    for held in range(1, exits.max_sessions_held + 1):
        index = signal_index + 1 + held
        if index >= len(bars):
            break
        outcome = _exit_on(bars[index], stop, target, direction)
        if outcome is not None:
            return outcome[0], outcome[1], held, index

    last = min(signal_index + 1 + exits.max_sessions_held, len(bars) - 1)
    if last <= signal_index + 1:
        return 0, ExitReason.TIME, 0, None
    return bars[last].close.paise, ExitReason.TIME, last - signal_index - 1, last


def _is_against(direction: Direction, regime: TrendRegime) -> bool:
    """A bullish trade in a downtrend, or a bearish trade in an uptrend.

    An unclear or ranging market is not "against" anything, so it reads False -
    the column answers "did this fight the trend", not "was there a trend".
    """
    if direction is Direction.BULLISH:
        return regime is TrendRegime.DOWNTREND
    return regime is TrendRegime.UPTREND


def run_strategy(
    strategy: Strategy,
    bars: Sequence[Bar],
    market: MarketContext,
    warmup: int,
    cost_fraction: float = DEFAULT_COST_FRACTION,
) -> list[SimulatedTrade]:
    """Every trade `strategy` would have taken in this series."""
    trades = []
    for index in range(warmup, len(bars) - 1):
        signal = strategy.signal_at(bars, index)
        if signal is None:
            continue
        trade = simulate_one(
            bars,
            index,
            signal=signal,
            strategy=strategy,
            market=market,
            cost_fraction=cost_fraction,
        )
        if trade is not None:
            trades.append(trade)
    return trades
