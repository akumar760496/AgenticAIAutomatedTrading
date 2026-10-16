"""In-memory market data. The test supplies the bars; nothing is invented."""

from __future__ import annotations

from datetime import datetime

from trading_desk.domain.instruments import InstrumentRef
from trading_desk.domain.market import Bar, Quote


class FakeMarketData:
    """Returns what a test put in. Freshness is a flag the test controls.

    is_fresh is settable so the fail-closed path can be tested without waiting for
    a real feed to break.
    """

    def __init__(self) -> None:
        self._bars: dict[str, list[Bar]] = {}
        self._quotes: dict[str, Quote] = {}
        self.fresh = True
        self.fetch_bars_calls = 0

    def add_bars(self, instrument: InstrumentRef, bars: list[Bar]) -> None:
        self._bars[instrument.symbol] = sorted(bars, key=lambda bar: bar.start)

    def set_quote(self, quote: Quote) -> None:
        self._quotes[quote.instrument.symbol] = quote

    def fetch_bars(
        self, instrument: InstrumentRef, interval: str, start: datetime, end: datetime
    ) -> list[Bar]:
        self.fetch_bars_calls += 1
        bars = self._bars.get(instrument.symbol, [])
        return [bar for bar in bars if start <= bar.start < end]

    def fetch_quote(self, instrument: InstrumentRef) -> Quote:
        quote = self._quotes.get(instrument.symbol)
        if quote is None:
            raise LookupError(f"No quote set for {instrument.symbol} - add one in the test")
        return quote

    def is_fresh(self, as_of: datetime) -> bool:
        return self.fresh
