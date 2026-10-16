"""What we need to read from the broker account.

READ ONLY, deliberately and structurally. There is no place_order, no modify, no
cancel - not commented out, absent. In v1 there is no interface through which an
order could be placed, so no amount of agent creativity or coding mistake can
place one.

The order path arrives in v2 as a separate port with its own approval gate. That
is FR-13.6: the control is the shape of the code, not a rule someone remembers.

Dhan documents no read-only token scope, so the token itself can trade. This port
is why that does not matter yet.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from trading_desk.domain.instruments import InstrumentRef
from trading_desk.domain.values import Money, Price, Quantity


@dataclass(frozen=True)
class BrokerPosition:
    """An open position as the broker reports it. The source of truth for reconciliation."""

    instrument: InstrumentRef
    quantity: Quantity
    average_price: Price
    unrealised_pnl: Money
    fetched_at: datetime


@dataclass(frozen=True)
class BrokerTrade:
    """A fill. This is what the user actually got, not what the briefing assumed.

    The gap between the two is real slippage, which replaces the modelled figure in
    the cost model - and that number decides whether either setup is viable.
    """

    trade_id: str
    instrument: InstrumentRef
    side: str  # BUY | SELL
    quantity: Quantity
    price: Price
    traded_at: datetime


class BrokerReadPort(Protocol):
    """Read-only view of the account. Named 'Read' so adding writes means a new port."""

    def fetch_positions(self) -> list[BrokerPosition]: ...

    def fetch_trades(self, since: datetime) -> list[BrokerTrade]:
        """Fills since `since`. Matched to briefing ideas by the sync job in step 1.10."""
        ...

    def fetch_funds(self) -> Money:
        """Available cash. Used for position sizing, never for an order."""
        ...
