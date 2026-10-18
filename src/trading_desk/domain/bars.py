"""Aggregating ticks into OHLCV bars.

Pure and batch: ticks in, bars out, no clock and no I/O. A live feed holds one
window's ticks in the application layer and calls this per window, so the same
code builds a bar from a websocket and from a backfill.

Three rules, each chosen because the alternative loses information:

1. **Nothing is interpolated.** A five-minute window with no trades produces no
   bar. An empty candle carrying the previous close is a fabricated price, and
   once written it is indistinguishable from a real one.
2. **Nothing is silently dropped.** Every tick that does not become part of a bar
   comes back in `rejected`, with the reason. A tick that vanishes is a volume
   discrepancy nobody can trace.
3. **Ticks are processed in the order given.** Sorting first would hide
   out-of-order arrival, which is a feed fault worth seeing. A tick that arrives
   after its bar has closed is recorded as late rather than quietly folded in.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from trading_desk.domain.instruments import InstrumentRef
from trading_desk.domain.market import Bar, Tick
from trading_desk.domain.sessions import TradingSession, bar_start
from trading_desk.domain.values import Price


class RejectReason(StrEnum):
    OUTSIDE_SESSION = "outside_session"
    LATE = "late"  # arrived after its bar had already closed
    DUPLICATE = "duplicate"  # same feed sequence number seen twice


@dataclass(frozen=True)
class RejectedTick:
    """A tick that did not reach a bar, and why."""

    tick: Tick
    reason: RejectReason


@dataclass(frozen=True)
class BarSeries:
    """The bars built, and everything that did not make it in."""

    bars: tuple[Bar, ...]
    rejected: tuple[RejectedTick, ...]

    @property
    def volume(self) -> int:
        return sum(bar.volume for bar in self.bars)

    def rejected_for(self, reason: RejectReason) -> tuple[RejectedTick, ...]:
        return tuple(item for item in self.rejected if item.reason is reason)

    def summary(self) -> str:
        return f"{len(self.bars)} bars, {self.volume} shares, {len(self.rejected)} ticks rejected"


@dataclass
class _Accumulator:
    """One bar under construction. Mutable by design, and never escapes this module."""

    start: datetime
    open: Price
    high: Price
    low: Price
    close: Price
    volume: int

    def add(self, tick: Tick) -> None:
        self.high = max(self.high, tick.price)
        self.low = min(self.low, tick.price)
        self.close = tick.price
        self.volume += tick.quantity


def build_bars(ticks: Sequence[Tick], interval_minutes: int, session: TradingSession) -> BarSeries:
    """Aggregate ticks into bars of `interval_minutes`, aligned to the session open.

    Every tick is accounted for: it lands in a bar or in `rejected`.
    """
    if not ticks:
        return BarSeries(bars=(), rejected=())

    instrument = ticks[0].instrument
    completed: list[Bar] = []
    rejected: list[RejectedTick] = []
    current: _Accumulator | None = None
    seen_sequences: set[int] = set()

    for tick in ticks:
        if tick.sequence is not None:
            if tick.sequence in seen_sequences:
                rejected.append(RejectedTick(tick, RejectReason.DUPLICATE))
                continue
            seen_sequences.add(tick.sequence)

        if not session.contains(tick.exchange_time):
            rejected.append(RejectedTick(tick, RejectReason.OUTSIDE_SESSION))
            continue

        start = bar_start(tick.exchange_time, interval_minutes, session)

        if current is None:
            current = _new_accumulator(start, tick)
            continue

        if start == current.start:
            current.add(tick)
            continue

        if start < current.start:
            # Its window has already been closed and emitted. Folding it in now
            # would change a bar that downstream code may already have read.
            rejected.append(RejectedTick(tick, RejectReason.LATE))
            continue

        completed.append(_finish(current, instrument))
        current = _new_accumulator(start, tick)

    if current is not None:
        completed.append(_finish(current, instrument))

    return BarSeries(bars=tuple(completed), rejected=tuple(rejected))


def _new_accumulator(start: datetime, tick: Tick) -> _Accumulator:
    return _Accumulator(
        start=start,
        open=tick.price,
        high=tick.price,
        low=tick.price,
        close=tick.price,
        volume=tick.quantity,
    )


def _finish(accumulator: _Accumulator, instrument: InstrumentRef) -> Bar:
    return Bar(
        instrument=instrument,
        start=accumulator.start,
        open=accumulator.open,
        high=accumulator.high,
        low=accumulator.low,
        close=accumulator.close,
        volume=accumulator.volume,
    )
