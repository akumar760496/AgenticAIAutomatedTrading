"""What we need from a market data source.

Says nothing about Dhan, websockets or HTTP. The live adapter subscribes to a
feed; the backtest adapter reads Parquet; the fake returns what a test handed it.
Application code cannot tell which it has, which is what makes one engine run in
three modes.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from trading_desk.domain.instruments import InstrumentRef
from trading_desk.domain.market import Bar, Quote


class MarketDataPort(Protocol):
    """Bars and quotes for instruments in the universe."""

    def fetch_bars(
        self,
        instrument: InstrumentRef,
        interval: str,
        start: datetime,
        end: datetime,
    ) -> list[Bar]:
        """Bars in [start, end), oldest first.

        Returns fewer bars than requested when data is missing. It never
        interpolates: a gap is a fact to record, not a hole to fill. Callers that
        need completeness must check for themselves.
        """
        ...

    def fetch_quote(self, instrument: InstrumentRef) -> Quote:
        """The latest quote. Raises if the feed is stale - see is_fresh()."""
        ...

    def is_fresh(self, as_of: datetime) -> bool:
        """Whether the feed is current enough to act on.

        The fail-closed rule lives on this: when it returns False, no ideas are
        produced. Silence beats a briefing built on old prices.
        """
        ...
