"""Where cached exchange expiries are kept.

The cache exists so that a gate asking "how many days to expiry" does not make a
network call, and so the list survives a restart. It is explicitly a cache of
something the exchange owns: nothing may write an expiry that was not read from
the venue (ISS-004).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from typing import Protocol


class ExpiryStorePort(Protocol):
    """Cached expiry lists, by instrument."""

    def save_expiries(
        self,
        symbol: str,
        expiries: Sequence[date],
        fetched_at: datetime,
        displaced: Sequence[date] = (),
    ) -> int:
        """Store or refresh this symbol's expiry list. Returns rows written."""
        ...

    def expiries_for(self, symbol: str) -> list[date]:
        """Cached expiries, soonest first. Empty if never fetched."""
        ...

    def last_fetched_at(self, symbol: str) -> datetime | None:
        """When the list was last refreshed, or None if never."""
        ...
