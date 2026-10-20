"""What is Setup A's technical_confirm gate actually worth?

Asked for on 2026-10-20. The owner wants 90% directional accuracy; `docs/07`
sets 40% as the *failure* line. Neither number has ever been measured, and this
is the only validation the stored data can support: a spread's profit and loss
needs historical option prices, and the archive holds two days of those
(ISS-037). Daily equity bars go back to January 2023, so the one question that
*can* be answered is the directional one.

**It calls the production gate, not a copy of it.** `technical_confirm` from
`domain/setups/setup_a.py`, with the thresholds from `config/setups.yaml`, over
the breakout and volume functions in `domain/indicators.py`. A backtest with its
own implementation of the rule measures a rule nobody trades.

**The headline number alone would be misleading, so it is never printed alone.**
Indian equities rose through most of 2023-2026. Any long-biased signal looks
good against that, so every hit rate is printed beside the unconditional base
rate for the same horizon and the same direction - what you would have got by
picking that day at random. The difference between the two is the only part that
is evidence of a signal.

Read-only. Touches no option data, spends nothing, writes nothing.

    uv run python -m scripts.backtest_technical
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from decimal import Decimal
from math import sqrt
from statistics import median

from trading_desk.adapters.store.bar_repository import PostgresBarStore
from trading_desk.application.universe import load_universe_request
from trading_desk.domain.enums import Direction
from trading_desk.domain.indicators import (
    DEFAULT_RANGE_SESSIONS,
    DEFAULT_VOLUME_PERIOD,
    closed_beyond_range,
    volume_ratio,
)
from trading_desk.domain.market import Bar
from trading_desk.domain.setups.setup_a import SetupACandidate, technical_confirm
from trading_desk.domain.setups.thresholds import SetupAThresholds, load_setup_thresholds
from trading_desk.infra.config import load_infra_settings
from trading_desk.infra.db import create_db_engine, create_session_factory

# One, two, three and four weeks of trading. The owner's stated holding period.
HORIZONS = (5, 10, 15, 20)

# Enough history for both halves of the gate before the first test can run.
WARMUP = max(DEFAULT_RANGE_SESSIONS, DEFAULT_VOLUME_PERIOD) + 1

# Daily bars stored per symbol. 940 trading days exist; read them all.
BARS_TO_READ = 2000

# Below this many standard errors from the base rate, an edge is noise. Two is
# the conventional bar and it is already generous for a result nobody
# pre-registered.
SIGMA_FOR_A_FINDING = 2.0


@dataclass
class Tally:
    """Hits and misses for one (direction, horizon) cell."""

    hits: int = 0
    total: int = 0
    moves: list[float] = field(default_factory=list)

    def add(self, forward_move: float, wanted_up: bool) -> None:
        self.total += 1
        self.moves.append(forward_move)
        if (forward_move > 0) == wanted_up:
            self.hits += 1

    @property
    def rate(self) -> float | None:
        return self.hits / self.total if self.total else None

    def median_move(self) -> float:
        return median(self.moves) if self.moves else 0.0


def forward_move(bars: list[Bar], index: int, horizon: int) -> float | None:
    """Percent change from bar `index`'s close to `horizon` bars later.

    None when the horizon runs past the end of the series, so the last four
    weeks of history contribute no rows rather than short ones.
    """
    if index + horizon >= len(bars):
        return None
    start = bars[index].close.paise
    if start <= 0:
        return None
    end = bars[index + horizon].close.paise
    return float(Decimal(end - start) / Decimal(start))


def break_direction(bars: list[Bar], index: int) -> Direction | None:
    """Which way the close broke, measured the same way the gate measures it."""
    window = bars[: index + 1]
    if len(window) < DEFAULT_RANGE_SESSIONS + 1:
        return None
    prior = window[-(DEFAULT_RANGE_SESSIONS + 1) : -1]
    close = window[-1].close.paise
    if close > max(bar.high.paise for bar in prior):
        return Direction.BULLISH
    if close < min(bar.low.paise for bar in prior):
        return Direction.BEARISH
    return None


def gate_fires(bars: list[Bar], index: int, limits: SetupAThresholds) -> Direction | None:
    """The production gate's verdict on bar `index`, and which way it broke.

    Returns the direction when `technical_confirm` passes, None otherwise. The
    window ends at `index`, so nothing after it is visible - the whole point.
    """
    window = bars[: index + 1]
    direction = break_direction(bars, index)
    if direction is None:
        return None

    candidate = SetupACandidate(
        instrument_symbol=bars[index].instrument.symbol,
        direction=direction,
        close_beyond_range_edge=closed_beyond_range(window, DEFAULT_RANGE_SESSIONS),
        volume_ratio=volume_ratio(window, DEFAULT_VOLUME_PERIOD),
    )
    result = technical_confirm(candidate, limits)
    return direction if result.passed else None


def walk(
    bars: list[Bar],
    limits: SetupAThresholds,
    fired: dict[tuple[Direction, int], Tally],
    baseline: dict[tuple[Direction, int], Tally],
) -> None:
    """Every testable bar in one symbol's history, into the two tallies.

    `baseline` gets every bar regardless of the gate, which is what makes the
    comparison fair: the same symbols, the same days, the same horizons.
    """
    for index in range(WARMUP, len(bars)):
        direction = gate_fires(bars, index, limits)
        for horizon in HORIZONS:
            move = forward_move(bars, index, horizon)
            if move is None:
                continue
            for side in (Direction.BULLISH, Direction.BEARISH):
                baseline[(side, horizon)].add(move, wanted_up=side is Direction.BULLISH)
            if direction is not None:
                fired[(direction, horizon)].add(move, wanted_up=direction is Direction.BULLISH)


def standard_errors_from_zero(shot: Tally, base_rate: float) -> float:
    """How many standard errors the hit rate sits from the base rate.

    Without this the table invites reading a 2% edge on 400 samples as a
    finding. Rule of thumb: under 2 is noise, and nothing here should be called
    an edge on fewer than that.
    """
    if shot.total == 0:
        return 0.0
    error = sqrt(base_rate * (1 - base_rate) / shot.total)
    if error == 0:
        return 0.0
    return ((shot.hits / shot.total) - base_rate) / error


def _print_table(
    fired: dict[tuple[Direction, int], Tally],
    baseline: dict[tuple[Direction, int], Tally],
) -> None:
    print(
        f"\n{'direction':<10} {'horizon':>8} {'fired':>7} {'hit rate':>9} "
        f"{'base rate':>10} {'edge':>7} {'sigma':>6} {'median move':>12}  verdict"
    )
    print("-" * 88)
    for side in (Direction.BULLISH, Direction.BEARISH):
        for horizon in HORIZONS:
            shot = fired[(side, horizon)]
            base = baseline[(side, horizon)]
            if not shot.total or base.rate is None or shot.rate is None:
                print(
                    f"{side.value:<10} {horizon:>6}d  {shot.total:>7} "
                    f"{'n/a':>9} {base.rate or 0:>9.1%} {'':>7} {'':>12}"
                )
                continue
            edge = shot.rate - base.rate
            sigma = standard_errors_from_zero(shot, base.rate)
            verdict = "noise" if abs(sigma) < SIGMA_FOR_A_FINDING else "distinguishable"
            print(
                f"{side.value:<10} {horizon:>6}d  {shot.total:>7} "
                f"{shot.rate:>9.1%} {base.rate:>10.1%} {edge:>+7.1%} "
                f"{sigma:>+6.1f} {shot.median_move():>+11.2%}  {verdict}"
            )


def main() -> int:
    limits = load_setup_thresholds().setup_a
    store = PostgresBarStore(create_session_factory(create_db_engine(load_infra_settings())))
    symbols = sorted(load_universe_request().stock_symbols)

    fired = {(side, h): Tally() for side in Direction for h in HORIZONS}
    baseline = {(side, h): Tally() for side in Direction for h in HORIZONS}

    print("Setup A technical_confirm, measured on stored daily bars")
    print(
        f"gate: close beyond the {DEFAULT_RANGE_SESSIONS}d range edge "
        f"AND volume >= {limits.min_volume_ratio}x the {DEFAULT_VOLUME_PERIOD}d average"
    )
    print(f"stocks: {len(symbols)} (indices excluded - Setup A is a stock setup)")

    days = 0
    for symbol in symbols:
        bars = store.daily_bars(symbol, BARS_TO_READ)
        if len(bars) <= WARMUP:
            print(f"  skipped {symbol}: only {len(bars)} bars")
            continue
        days += len(bars) - WARMUP
        walk(bars, limits, fired, baseline)

    print(f"bar-days tested: {days:,}")
    _print_table(fired, baseline)

    total_fires = sum(fired[(side, HORIZONS[0])].total for side in Direction)
    rate = total_fires / days if days else 0.0
    print(f"\nthe gate fired on {total_fires:,} of {days:,} bar-days ({rate:.1%})")
    print("'edge' is hit rate minus base rate; 'sigma' is how many standard errors")
    print(f"that edge sits from zero. Under {SIGMA_FOR_A_FINDING:.0f} it is noise, however")
    print("large the hit rate looks on its own.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
