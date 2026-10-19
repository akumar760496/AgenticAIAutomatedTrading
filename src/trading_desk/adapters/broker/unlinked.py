"""An account the system cannot see, said out loud.

v1 has no broker sync - that is step 1.10 - and Ajay places every trade by
hand. So the system genuinely does not know what he is holding, and the
Portfolio Context agent needs *something* behind its port.

The dangerous version of this class returns empty lists and lets the briefing
print "0 open positions", which is a statement about the account rather than
about our ignorance, and is false the first morning he is carrying a spread.
So this one is **named for what it is**, and every cycle wired to it adds a
line to the briefing's missing-inputs section saying the portfolio is unknown.

Replace it with a real Dhan read adapter in step 1.10 and that line disappears
on its own.
"""

from __future__ import annotations

from datetime import datetime

from trading_desk.domain.values import Money
from trading_desk.ports.broker import BrokerPosition, BrokerTrade

NOT_LINKED = (
    "broker not linked (step 1.10): open positions, exposure and net delta are "
    "unknown, and the portfolio section reads as empty rather than as flat"
)


class UnlinkedBroker:
    """Implements BrokerReadPort by knowing nothing, visibly."""

    def fetch_positions(self) -> list[BrokerPosition]:
        return []

    def fetch_trades(self, since: datetime) -> list[BrokerTrade]:
        del since
        return []

    def fetch_funds(self) -> Money:
        """Zero, and the missing-inputs line explains that zero means unknown."""
        return Money.zero()
