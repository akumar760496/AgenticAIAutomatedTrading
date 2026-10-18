"""In-memory cached expiries."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime


class FakeExpiryStore:
    """Upserts like the real store, so a refresh is idempotent in row count."""

    def __init__(self) -> None:
        self.expiries: dict[str, list[date]] = {}
        self.displaced: dict[str, set[date]] = {}
        self.fetched: dict[str, datetime] = {}

    def save_expiries(
        self,
        symbol: str,
        expiries: Sequence[date],
        fetched_at: datetime,
        displaced: Sequence[date] = (),
    ) -> int:
        self.expiries[symbol] = sorted(expiries)
        self.displaced[symbol] = set(displaced)
        self.fetched[symbol] = fetched_at
        return len(expiries)

    def expiries_for(self, symbol: str) -> list[date]:
        return list(self.expiries.get(symbol, []))

    def last_fetched_at(self, symbol: str) -> datetime | None:
        return self.fetched.get(symbol)
