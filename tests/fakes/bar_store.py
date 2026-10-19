"""In-memory daily bars, idempotent like the real store."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from decimal import Decimal

from trading_desk.domain.market import Bar
from trading_desk.domain.timezones import to_ist


class FakeBarStore:
    """Keyed by (symbol, trading date), so a second write of the same day is a no-op.

    The real store gets this from a primary key plus ON CONFLICT DO NOTHING. The
    fake reproduces the behaviour rather than the mechanism, so a test that passes
    here is testing the same guarantee.
    """

    def __init__(self) -> None:
        self.bars: dict[tuple[str, date], Bar] = {}
        self.sources: dict[tuple[str, date], str] = {}

    def write_daily_bars(self, bars: Sequence[Bar], source: str = "backfill") -> int:
        inserted = 0
        for bar in bars:
            key = (bar.instrument.symbol, to_ist(bar.start).date())
            if key in self.bars:
                continue
            self.bars[key] = bar
            self.sources[key] = source
            inserted += 1
        return inserted

    def count_daily_bars(self, symbol: str) -> int:
        return sum(1 for stored_symbol, _ in self.bars if stored_symbol == symbol)

    def daily_bar_dates(self, symbol: str) -> list[date]:
        return sorted(day for stored_symbol, day in self.bars if stored_symbol == symbol)

    def daily_closes(self, symbol: str) -> list[tuple[date, Decimal]]:
        matching = sorted(
            (day, bar) for (stored, day), bar in self.bars.items() if stored == symbol
        )
        return [(day, bar.close.rupees()) for day, bar in matching]

    def daily_bars(self, symbol: str, limit: int) -> list[Bar]:
        """The last `limit` bars, oldest first - the same order as the real store."""
        ordered = [bar for (stored, _), bar in sorted(self.bars.items()) if stored == symbol]
        return ordered[-limit:]
