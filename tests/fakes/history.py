"""In-memory historical bars."""

from __future__ import annotations

from datetime import date

from trading_desk.domain.instruments import InstrumentRef
from trading_desk.domain.market import Bar
from trading_desk.domain.timezones import to_ist


class FakeHistory:
    """Bars a test supplies, keyed by symbol.

    fail_next_fetch exists so the backfill's per-instrument error handling can be
    exercised: one unavailable symbol must not abandon the rest of the universe.
    """

    def __init__(self) -> None:
        self._bars: dict[str, list[Bar]] = {}
        self.fail_next_fetch = False
        self.calls = 0

    def add_bars(self, symbol: str, bars: list[Bar]) -> None:
        self._bars[symbol] = bars

    def daily_bars(self, instrument: InstrumentRef, start: date, end: date) -> list[Bar]:
        self.calls += 1
        if self.fail_next_fetch:
            self.fail_next_fetch = False
            raise ConnectionError("Simulated history fetch failure")
        bars = self._bars.get(instrument.symbol, [])
        return [bar for bar in bars if start <= to_ist(bar.start).date() <= end]
