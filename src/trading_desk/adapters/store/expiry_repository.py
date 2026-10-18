"""Cached exchange expiries in Postgres.

Upsert rather than insert-or-skip, unlike the bar store. A bar for a past day is
immutable, so re-writing it is pointless; an expiry row can legitimately change -
`fetched_at` moves forward every refresh, and `is_displaced` can flip if the
exchange reissues a list after a holiday notification. The primary key still
makes the operation idempotent in row count.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from trading_desk.infra.db import session_scope

UPSERT_EXPIRY = text(
    """
    INSERT INTO expiries (instrument_symbol, expiry_date, fetched_at, is_displaced)
    VALUES (:symbol, :expiry_date, :fetched_at, :is_displaced)
    ON CONFLICT (instrument_symbol, expiry_date) DO UPDATE
        SET fetched_at = EXCLUDED.fetched_at,
            is_displaced = EXCLUDED.is_displaced
    """
)


class PostgresExpiryStore:
    """Implements ExpiryStorePort against the expiries table."""

    def __init__(self, factory: sessionmaker[Session]) -> None:
        self._factory = factory

    def save_expiries(
        self,
        symbol: str,
        expiries: Sequence[date],
        fetched_at: datetime,
        displaced: Sequence[date] = (),
    ) -> int:
        if not expiries:
            return 0

        displaced_set = set(displaced)
        rows = [
            {
                "symbol": symbol,
                "expiry_date": expiry,
                "fetched_at": fetched_at,
                "is_displaced": expiry in displaced_set,
            }
            for expiry in expiries
        ]

        with session_scope(self._factory) as session:
            session.execute(UPSERT_EXPIRY, rows)
        return len(rows)

    def expiries_for(self, symbol: str) -> list[date]:
        with session_scope(self._factory) as session:
            rows = session.execute(
                text(
                    "SELECT expiry_date FROM expiries WHERE instrument_symbol = :symbol "
                    "ORDER BY expiry_date"
                ),
                {"symbol": symbol},
            ).all()
        return [row[0] for row in rows]

    def last_fetched_at(self, symbol: str) -> datetime | None:
        """When this symbol's list was last refreshed, or None if never."""
        with session_scope(self._factory) as session:
            return session.execute(
                text("SELECT max(fetched_at) FROM expiries WHERE instrument_symbol = :symbol"),
                {"symbol": symbol},
            ).scalar_one_or_none()
