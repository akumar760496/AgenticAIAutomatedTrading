"""Daily bars in Postgres, via the bars_1d hypertable.

Idempotency comes from the table's primary key (instrument_symbol, bar_start) and
`ON CONFLICT DO NOTHING`, not from reading first and then writing. A read-then-write
has a race and, more practically, doubles the round trips on a 694-bar load.

`source` is stored per row because a bar from this backfill and a bar from the
live feed can disagree about the same day, and without it nobody can tell which
one they are looking at.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from trading_desk.domain.enums import Exchange, Segment
from trading_desk.domain.instruments import InstrumentRef
from trading_desk.domain.market import Bar
from trading_desk.domain.timezones import to_ist
from trading_desk.domain.values import PAISE_PER_RUPEE, Price
from trading_desk.infra.db import session_scope

SELECT_RECENT_BARS = text(
    """
    SELECT security_id, bar_start, open_paise, high_paise, low_paise, close_paise, volume
    FROM bars_1d
    WHERE instrument_symbol = :symbol
    ORDER BY bar_start DESC
    LIMIT :limit
    """
)

INSERT_BAR = text(
    """
    INSERT INTO bars_1d (
        instrument_symbol, security_id, bar_start,
        open_paise, high_paise, low_paise, close_paise, volume, source
    ) VALUES (
        :symbol, :security_id, :bar_start,
        :open_paise, :high_paise, :low_paise, :close_paise, :volume, :source
    )
    ON CONFLICT (instrument_symbol, bar_start) DO NOTHING
    """
)


class PostgresBarStore:
    """Implements BarStorePort against the bars_1d hypertable."""

    def __init__(self, factory: sessionmaker[Session]) -> None:
        self._factory = factory

    def write_daily_bars(self, bars: Sequence[Bar], source: str = "backfill") -> int:
        if not bars:
            return 0

        rows = [
            {
                "symbol": bar.instrument.symbol,
                "security_id": bar.instrument.security_id,
                "bar_start": bar.start,
                "open_paise": bar.open.paise,
                "high_paise": bar.high.paise,
                "low_paise": bar.low.paise,
                "close_paise": bar.close.paise,
                "volume": bar.volume,
                "source": source,
            }
            for bar in bars
        ]

        with session_scope(self._factory) as session:
            before = _count(session, bars[0].instrument.symbol)
            session.execute(INSERT_BAR, rows)
            session.flush()
            after = _count(session, bars[0].instrument.symbol)

        return after - before

    def count_daily_bars(self, symbol: str) -> int:
        with session_scope(self._factory) as session:
            return _count(session, symbol)

    def daily_closes(self, symbol: str) -> list[tuple[date, Decimal]]:
        """Closing prices oldest first, for the realised-vol proxy (FR-5.6).

        Paise are converted to Decimal rupees here rather than float: the log
        returns downstream are float maths by nature, but the price must not
        have been a float on the way in (CLAUDE.md rule 4).
        """
        with session_scope(self._factory) as session:
            rows = session.execute(
                text(
                    "SELECT bar_start, close_paise FROM bars_1d "
                    "WHERE instrument_symbol = :symbol ORDER BY bar_start"
                ),
                {"symbol": symbol},
            ).all()
        return [(to_ist(row[0]).date(), Decimal(row[1]) / PAISE_PER_RUPEE) for row in rows]

    def daily_bar_dates(self, symbol: str) -> list[date]:
        with session_scope(self._factory) as session:
            rows = session.execute(
                text(
                    "SELECT bar_start FROM bars_1d WHERE instrument_symbol = :symbol "
                    "ORDER BY bar_start"
                ),
                {"symbol": symbol},
            ).all()
        # The stored instant is midnight IST; converting back is what makes the
        # returned date the trading date rather than the UTC calendar date.
        return [to_ist(row[0]).date() for row in rows]

    def daily_bars(self, symbol: str, limit: int) -> list[Bar]:
        """The most recent `limit` bars, oldest first.

        `ORDER BY ... DESC LIMIT n` then reverse, rather than reading the whole
        series and slicing: the point of the limit is to avoid pulling 940 rows
        to compute a 14-period ATR.
        """
        with session_scope(self._factory) as session:
            rows = session.execute(SELECT_RECENT_BARS, {"symbol": symbol, "limit": limit}).all()
        return [_to_bar(symbol, row) for row in reversed(rows)]


def _count(session: Session, symbol: str) -> int:
    result = session.execute(
        text("SELECT count(*) FROM bars_1d WHERE instrument_symbol = :symbol"),
        {"symbol": symbol},
    ).scalar_one()
    return int(result)


def _to_bar(symbol: str, row: Any) -> Bar:
    """Rebuild a Bar from a bars_1d row.

    The table stores the symbol and the security id, not the exchange, segment
    or lot size, so those three are reconstructed. **The lot size here is a
    placeholder and nothing may size from it** - sizing reads the instrument
    master, which is the only authoritative source for it. Nothing in
    `domain/indicators.py` touches the instrument beyond its symbol, which is
    why rebuilding a partial ref is safe for this one path.
    """
    security_id, start, open_paise, high_paise, low_paise, close_paise, volume = row
    instrument = InstrumentRef(
        symbol=symbol,
        exchange=Exchange.NSE,
        segment=Segment.EQUITY,
        security_id=str(security_id),
    )
    return Bar(
        instrument=instrument,
        start=start,
        open=Price(paise=int(open_paise)),
        high=Price(paise=int(high_paise)),
        low=Price(paise=int(low_paise)),
        close=Price(paise=int(close_paise)),
        volume=int(volume),
    )
