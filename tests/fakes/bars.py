"""Build bar series from closes. Shared by tests and the verify scripts."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from trading_desk.domain.enums import Exchange, Segment
from trading_desk.domain.instruments import InstrumentRef
from trading_desk.domain.market import Bar
from trading_desk.domain.timezones import IST
from trading_desk.domain.values import Price

DEFAULT_INSTRUMENT = InstrumentRef(
    symbol="RELIANCE", exchange=Exchange.NSE, segment=Segment.EQUITY, security_id="1", lot_size=1
)
START = datetime(2026, 1, 1, tzinfo=IST)


def bars_from_closes(
    closes: list[Decimal],
    volumes: list[int] | None = None,
    instrument: InstrumentRef = DEFAULT_INSTRUMENT,
) -> list[Bar]:
    """One daily bar per close. OHLC are flat unless a test needs otherwise."""
    series = volumes or [1000] * len(closes)
    bars = []
    for day, close in enumerate(closes):
        price = Price.from_rupees(close)
        bars.append(
            Bar(
                instrument=instrument,
                start=START + timedelta(days=day),
                open=price,
                high=price,
                low=price,
                close=price,
                volume=series[day],
            )
        )
    return bars
