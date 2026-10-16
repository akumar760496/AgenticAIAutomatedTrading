"""What we need from an option chain source.

The most important port in v1: step 1.1 calls it three times a session, and the
series it produces becomes the IV-rank history that both setups gate on. That
history cannot be bought back or derived from price data.
"""

from __future__ import annotations

from datetime import date
from typing import Protocol

from trading_desk.domain.instruments import InstrumentRef
from trading_desk.domain.market import OptionChainSnapshot


class OptionChainPort(Protocol):
    """Full option chains with greeks."""

    def fetch_chain(self, underlying: InstrumentRef, expiry: date) -> OptionChainSnapshot:
        """The whole chain for one underlying and expiry, at this instant.

        Returns every strike the exchange lists, not a window around spot. Trimming
        happens later, in code we can change, rather than in captured data we cannot.
        """
        ...

    def available_expiries(self, underlying: InstrumentRef) -> list[date]:
        """Expiries currently listed, soonest first.

        Read from the feed rather than computed: NIFTY is weekly on Tuesday and
        BANKNIFTY is monthly only, and both rules have changed within two years.
        """
        ...
