"""What we need from a source of historical bars.

Separate from MarketDataPort on purpose. A history API answers "what happened
between these dates"; a live feed answers "what is happening now" and can say
whether it is stale. One Protocol covering both would force every historical
adapter to implement a freshness method it cannot honestly answer.
"""

from __future__ import annotations

from datetime import date
from typing import Protocol

from trading_desk.domain.instruments import InstrumentRef
from trading_desk.domain.market import Bar


class HistoricalBarsPort(Protocol):
    """Bars for a past date range."""

    def daily_bars(self, instrument: InstrumentRef, start: date, end: date) -> list[Bar]:
        """Daily bars covering [start, end], oldest first.

        Returns only the days the venue traded. Missing days are absent rather
        than filled: a gap is a fact about the data, and the caller decides
        whether it is a holiday or a problem.
        """
        ...
