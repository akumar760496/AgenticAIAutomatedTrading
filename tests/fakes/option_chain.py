"""In-memory option chains."""

from __future__ import annotations

from datetime import date

from trading_desk.domain.instruments import InstrumentRef
from trading_desk.domain.market import OptionChainSnapshot


class FakeOptionChain:
    """Chains a test supplies, keyed by (symbol, expiry).

    fail_next_fetch exists so the snapshotter's error handling can be exercised -
    a snapshotter that fails silently is worse than none, because the gap is
    trusted.
    """

    def __init__(self) -> None:
        self._chains: dict[tuple[str, date], OptionChainSnapshot] = {}
        self.fail_next_fetch = False
        self.fetch_calls = 0

    def add_chain(self, chain: OptionChainSnapshot, expiry: date) -> None:
        self._chains[(chain.underlying.symbol, expiry)] = chain

    def fetch_chain(self, underlying: InstrumentRef, expiry: date) -> OptionChainSnapshot:
        self.fetch_calls += 1
        if self.fail_next_fetch:
            self.fail_next_fetch = False
            raise ConnectionError("Simulated chain fetch failure")
        chain = self._chains.get((underlying.symbol, expiry))
        if chain is None:
            raise LookupError(f"No chain for {underlying.symbol} {expiry} - add one in the test")
        return chain

    def available_expiries(self, underlying: InstrumentRef) -> list[date]:
        return sorted(expiry for symbol, expiry in self._chains if symbol == underlying.symbol)
