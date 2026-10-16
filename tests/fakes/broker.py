"""A read-only broker account in memory.

Has no order methods, exactly like the real port. If a test ever needs to place an
order through this, that test is testing something v1 does not do.
"""

from __future__ import annotations

from datetime import datetime

from trading_desk.domain.values import Money
from trading_desk.ports.broker import BrokerPosition, BrokerTrade


class FakeBrokerRead:
    def __init__(self) -> None:
        self.positions: list[BrokerPosition] = []
        self.trades: list[BrokerTrade] = []
        self.funds = Money.zero()

    def fetch_positions(self) -> list[BrokerPosition]:
        return list(self.positions)

    def fetch_trades(self, since: datetime) -> list[BrokerTrade]:
        return [trade for trade in self.trades if trade.traded_at > since]

    def fetch_funds(self) -> Money:
        return self.funds
