"""Where bars are kept.

`write_daily_bars` is required to be idempotent: loading the same range twice
must leave the same rows. Re-running a backfill is the normal way to fill a gap,
and a loader that duplicates on the second run is the most common data bug there
is - and the hardest to spot, because every count is merely too large rather than
obviously wrong.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from typing import Protocol

from trading_desk.domain.market import Bar


class BarStorePort(Protocol):
    """Durable daily bars."""

    def write_daily_bars(self, bars: Sequence[Bar], source: str) -> int:
        """Store bars, skipping any already present. Returns the number inserted.

        The return value is inserts, not bars offered, so a caller can tell a
        first load from a no-op re-run.
        """
        ...

    def count_daily_bars(self, symbol: str) -> int: ...

    def daily_closes(self, symbol: str) -> list[tuple[date, Decimal]]:
        """Closing prices oldest first, in rupees. Feeds the realised-vol proxy."""
        ...

    def daily_bar_dates(self, symbol: str) -> list[date]:
        """Trading dates held for this symbol, oldest first."""
        ...

    def daily_bars(self, symbol: str, limit: int) -> list[Bar]:
        """The most recent `limit` bars, returned **oldest first**.

        Closes alone cannot give an ATR, a true range or a volume ratio, all of
        which the technical agent needs, so the full OHLCV row has to come back.

        The ordering is the part worth stating: `limit` counts back from the
        latest bar, but the list reads forward in time, because every indicator
        in `domain/indicators.py` assumes chronological order and a reversed
        series produces a plausible number rather than an error.
        """
        ...
