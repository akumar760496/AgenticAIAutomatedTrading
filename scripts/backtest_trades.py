"""Trade-by-trade simulation of Setup A's technical signal, on the underlying.

Asked for on 2026-10-20: entry price, exit price, profit or loss, win rate.

**This is the equity trade, not the options trade, and the difference matters.**
Setup A proposes a debit vertical, and its configured exits in
`config/setups.yaml` are fractions of the *spread's premium* (60% of width as
target, 50% of debit as stop). Reproducing those needs historical option prices
and the archive holds two days of them (ISS-037). So the exits below are mine,
not the config's, and they are stated in `EXIT_RULES` so nobody mistakes them
for signed-off numbers. A real spread on the same signal would do **worse**
than this: it pays a bid-ask spread on two legs and loses time value daily,
which ISS-023 measured at ~13% of a spread's best case.

Three choices that keep the simulation honest rather than flattering:

1. **Entry is the next bar's open, never the signal bar's close.** The signal
   *is* that close; you cannot trade a price you needed in order to decide.
2. **When a bar's range contains both the stop and the target, the stop wins.**
   Daily bars cannot say which came first, and assuming the favourable fill is
   the most common way a backtest lies.
3. **Costs are applied, both sides.** A gross win rate on a strategy whose
   median move is under 1% is not a result.

Read-only. Spends nothing, writes nothing.

    uv run python -m scripts.backtest_trades
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from statistics import mean

from scripts.backtest_technical import BARS_TO_READ, WARMUP, gate_fires
from trading_desk.adapters.store.bar_repository import PostgresBarStore
from trading_desk.application.universe import load_universe_request
from trading_desk.domain.enums import Direction
from trading_desk.domain.indicators import DEFAULT_ATR_PERIOD, average_true_range
from trading_desk.domain.market import Bar
from trading_desk.domain.setups.thresholds import load_setup_thresholds
from trading_desk.infra.config import load_infra_settings
from trading_desk.infra.db import create_db_engine, create_session_factory

# My exit rules, not the config's. See the module docstring.
STOP_IN_ATR = 1.0
TARGET_IN_ATR = 1.5
MAX_SESSIONS_HELD = 20  # four weeks, the owner's stated maximum horizon

EXIT_RULES = (
    f"stop {STOP_IN_ATR}x ATR({DEFAULT_ATR_PERIOD}) against, "
    f"target {TARGET_IN_ATR}x ATR in favour, "
    f"time stop {MAX_SESSIONS_HELD} sessions"
)

# Round trip on equity delivery: brokerage, STT, exchange fees, GST, stamp duty,
# plus slippage. A deliberately light figure - an options spread costs far more.
COST_FRACTION_PER_TRADE = 0.0025


@dataclass(frozen=True)
class Trade:
    symbol: str
    direction: Direction
    entry_date: str
    entry_paise: int
    exit_date: str
    exit_paise: int
    reason: str
    sessions_held: int

    @property
    def gross_return(self) -> float:
        """Signed return in the direction traded."""
        move = (self.exit_paise - self.entry_paise) / self.entry_paise
        return move if self.direction is Direction.BULLISH else -move

    @property
    def net_return(self) -> float:
        return self.gross_return - COST_FRACTION_PER_TRADE

    @property
    def won(self) -> bool:
        return self.net_return > 0

    def as_row(self) -> str:
        return (
            f"{self.symbol:<11} {self.direction.value:<8} {self.entry_date} "
            f"{self.entry_paise / 100:>9,.2f} {self.exit_date} "
            f"{self.exit_paise / 100:>9,.2f} {self.sessions_held:>3}d "
            f"{self.net_return:>+7.2%}  {self.reason}"
        )


def simulate(bars: list[Bar], signal_index: int, direction: Direction) -> Trade | None:
    """One trade from the bar after the signal until a stop, target or time stop."""
    atr = average_true_range(bars[: signal_index + 1])
    if atr is None or atr.paise <= 0 or signal_index + 1 >= len(bars):
        return None

    entry_bar = bars[signal_index + 1]
    entry = entry_bar.open.paise
    long = direction is Direction.BULLISH
    stop = entry - int(STOP_IN_ATR * atr.paise) if long else entry + int(STOP_IN_ATR * atr.paise)
    target = (
        entry + int(TARGET_IN_ATR * atr.paise) if long else entry - int(TARGET_IN_ATR * atr.paise)
    )

    for held in range(1, MAX_SESSIONS_HELD + 1):
        index = signal_index + 1 + held
        if index >= len(bars):
            break
        bar = bars[index]
        hit_stop = bar.low.paise <= stop if long else bar.high.paise >= stop
        hit_target = bar.high.paise >= target if long else bar.low.paise <= target
        if hit_stop:  # checked first: a bar holding both cannot say which came first
            return _trade(
                bars,
                signal_index=signal_index,
                exit_index=index,
                direction=direction,
                entry=entry,
                exit_paise=stop,
                reason="STOP",
                held=held,
            )
        if hit_target:
            return _trade(
                bars,
                signal_index=signal_index,
                exit_index=index,
                direction=direction,
                entry=entry,
                exit_paise=target,
                reason="TARGET",
                held=held,
            )

    last = min(signal_index + 1 + MAX_SESSIONS_HELD, len(bars) - 1)
    if last <= signal_index + 1:
        return None
    return _trade(
        bars,
        signal_index=signal_index,
        exit_index=last,
        direction=direction,
        entry=entry,
        exit_paise=bars[last].close.paise,
        reason="TIME",
        held=last - signal_index - 1,
    )


def _trade(
    bars: list[Bar],
    *,
    signal_index: int,
    exit_index: int,
    direction: Direction,
    entry: int,
    exit_paise: int,
    reason: str,
    held: int,
) -> Trade:
    return Trade(
        symbol=bars[signal_index].instrument.symbol,
        direction=direction,
        entry_date=str(bars[signal_index + 1].start.date()),
        entry_paise=entry,
        exit_date=str(bars[exit_index].start.date()),
        exit_paise=exit_paise,
        reason=reason,
        sessions_held=held,
    )


def collect(symbols: list[str], store: PostgresBarStore) -> list[Trade]:
    limits = load_setup_thresholds().setup_a
    trades = []
    for symbol in symbols:
        bars = store.daily_bars(symbol, BARS_TO_READ)
        if len(bars) <= WARMUP:
            continue
        for index in range(WARMUP, len(bars) - 1):
            direction = gate_fires(bars, index, limits)
            if direction is None:
                continue
            trade = simulate(bars, index, direction)
            if trade is not None:
                trades.append(trade)
    return trades


def summarise(label: str, trades: list[Trade]) -> None:
    if not trades:
        print(f"\n{label}: no trades")
        return
    wins = [t for t in trades if t.won]
    losses = [t for t in trades if not t.won]
    expectancy = mean(t.net_return for t in trades)
    reasons = {r: sum(1 for t in trades if t.reason == r) for r in ("TARGET", "STOP", "TIME")}

    print(f"\n{label}")
    print(f"  trades            {len(trades):>8,}")
    print(f"  win rate          {len(wins) / len(trades):>8.1%}   <- 'percentage of success'")
    print(f"  average win       {mean(t.net_return for t in wins) if wins else 0:>+8.2%}")
    print(f"  average loss      {mean(t.net_return for t in losses) if losses else 0:>+8.2%}")
    print(f"  expectancy/trade  {expectancy:>+8.2%}   <- the number that pays you")
    print(f"  total return      {sum(t.net_return for t in trades):>+8.1%}   (1 unit per trade)")
    print(
        f"  exits             target {reasons['TARGET']}, stop {reasons['STOP']}, time {reasons['TIME']}"
    )


def per_symbol(trades: list[Trade]) -> None:
    print(f"\n{'symbol':<11} {'trades':>7} {'win rate':>9} {'expectancy':>11} {'total':>9}")
    print("-" * 52)
    for symbol in sorted({t.symbol for t in trades}):
        mine = [t for t in trades if t.symbol == symbol]
        rate = sum(1 for t in mine if t.won) / len(mine)
        print(
            f"{symbol:<11} {len(mine):>7} {rate:>9.1%} "
            f"{mean(t.net_return for t in mine):>+11.2%} "
            f"{sum(t.net_return for t in mine):>+9.1%}"
        )


def main() -> int:
    store = PostgresBarStore(create_session_factory(create_db_engine(load_infra_settings())))
    stocks = sorted(load_universe_request().stock_symbols)
    indices = list(load_universe_request().index_symbols)

    print("Setup A technical signal, simulated trade by trade on the UNDERLYING")
    print(f"exits  : {EXIT_RULES}  (mine, not config - see the docstring)")
    print(f"costs  : {COST_FRACTION_PER_TRADE:.2%} round trip, applied to every trade")
    print("entry  : the open of the bar AFTER the signal")

    stock_trades = collect(stocks, store)
    index_trades = collect(indices, store)

    summarise(f"STOCKS ({len(stocks)} names)", stock_trades)
    summarise(f"INDICES ({', '.join(indices)})", index_trades)
    per_symbol(stock_trades)

    print(
        f"\nLast 15 stock trades in detail "
        f"({'symbol':<4} dir  entry date / price  exit date / price  held  net  why)"
    )
    print("-" * 96)
    for trade in sorted(stock_trades, key=lambda t: t.entry_date)[-15:]:
        print(trade.as_row())

    if index_trades:
        print(f"\nEvery index trade ({len(index_trades)} in ~4 years)")
        print("-" * 96)
        for trade in sorted(index_trades, key=lambda t: t.entry_date):
            print(trade.as_row())
    return 0


if __name__ == "__main__":
    sys.exit(main())
