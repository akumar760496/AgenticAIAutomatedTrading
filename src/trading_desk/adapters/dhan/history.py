"""Dhan's historical bars, mapped into domain Bars.

The response is six parallel arrays - open, high, low, close, volume, timestamp -
rather than a list of records, so the mapping zips them and trusts their order.
A length mismatch between them would silently pair the wrong price with the wrong
day, so it is checked rather than assumed.

Two conversions here are the whole reason this adapter exists:

1. **Prices arrive as JSON floats** (`1309.0`, `1313.1`). They go through
   `Decimal(str(value))`, never `Decimal(value)`: the latter preserves the binary
   float's error and defeats the point (CLAUDE.md rule 4).
2. **Timestamps are a true UTC epoch landing on midnight IST** of the trading
   date. Verified 2026-10-18: 1788201000 is 2026-08-31 18:30 UTC, which is
   2026-09-01 00:00 IST. Reading `.date()` off the UTC value gives 2026-08-31 -
   **every daily bar off by one day**, consistently, in a way that reconciles
   perfectly against itself.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from trading_desk.adapters.dhan.chain import INDEX_SYMBOLS, SEGMENT_INDEX
from trading_desk.adapters.dhan.client import DhanClient
from trading_desk.domain.instruments import InstrumentRef
from trading_desk.domain.market import Bar
from trading_desk.domain.timezones import IST
from trading_desk.domain.values import Price

HISTORICAL_PATH = "/charts/historical"

SEGMENT_NSE_EQ = "NSE_EQ"

ARRAY_FIELDS = ("open", "high", "low", "close", "volume", "timestamp")


class DhanHistoryAdapter:
    """Implements HistoricalBarsPort against Dhan's charts API."""

    def __init__(self, client: DhanClient) -> None:
        self._client = client

    def daily_bars(self, instrument: InstrumentRef, start: date, end: date) -> list[Bar]:
        if start > end:
            raise ValueError(f"{instrument.symbol}: start {start} is after end {end}")

        segment, kind = _segment_and_kind(instrument)

        body = self._client.post(
            HISTORICAL_PATH,
            {
                "securityId": instrument.security_id,
                "exchangeSegment": segment,
                "instrument": kind,
                "fromDate": start.isoformat(),
                "toDate": end.isoformat(),
            },
            # A different endpoint from the option chain, with its own budget.
            rate_limited=False,
        )
        return _map_bars(instrument, body)


def _segment_and_kind(instrument: InstrumentRef) -> tuple[str, str]:
    """Dhan needs both the segment and the instrument kind, and they move together.

    An index is not tradeable, so its history lives in the index segment rather
    than the equity one. Asking for NIFTY as NSE_EQ returns nothing rather than
    an error, which is why these are chosen from one place.
    """
    if instrument.symbol in INDEX_SYMBOLS:
        return SEGMENT_INDEX, "INDEX"
    return SEGMENT_NSE_EQ, "EQUITY"


def _map_bars(instrument: InstrumentRef, body: dict[str, Any]) -> list[Bar]:
    arrays = {field: body.get(field) or [] for field in ARRAY_FIELDS}

    lengths = {field: len(values) for field, values in arrays.items()}
    if len(set(lengths.values())) > 1:
        raise ValueError(f"{instrument.symbol}: history arrays disagree in length: {lengths}")

    bars = []
    for index in range(lengths["timestamp"]):
        bars.append(
            Bar(
                instrument=instrument,
                start=_trading_day_start(arrays["timestamp"][index]),
                open=_price(arrays["open"][index]),
                high=_price(arrays["high"][index]),
                low=_price(arrays["low"][index]),
                close=_price(arrays["close"][index]),
                volume=int(arrays["volume"][index]),
            )
        )
    return bars


def _price(value: Any) -> Price:
    # str() first: Decimal(1313.1) keeps the float's error, Decimal("1313.1") does not.
    return Price.from_rupees(Decimal(str(value)))


def _trading_day_start(epoch_seconds: float) -> datetime:
    """Midnight IST of the bar's trading date, as a tz-aware instant.

    Kept as the IST midnight instant rather than reduced to a date, because
    `Bar.start` is a tz-aware datetime and every consumer converts with `to_ist`.
    """
    moment = datetime.fromtimestamp(epoch_seconds, UTC)
    return moment.astimezone(IST)
