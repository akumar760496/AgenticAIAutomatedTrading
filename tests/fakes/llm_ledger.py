"""An in-memory spend ledger."""

from __future__ import annotations

from trading_desk.domain.timezones import to_ist
from trading_desk.domain.values import Money
from trading_desk.ports.llm_ledger import LedgerEntry


class FakeLlmLedger:
    def __init__(self) -> None:
        self.entries: list[LedgerEntry] = []

    def record(self, entry: LedgerEntry) -> None:
        self.entries.append(entry)

    def spend_in_month(self, year: int, month: int) -> Money:
        """Grouped in IST, matching the Postgres ledger's SQL."""
        total = Money.zero()
        for entry in self.entries:
            stamp = to_ist(entry.called_at)
            if stamp.year == year and stamp.month == month:
                total = total + entry.cost
        return total

    def entries_for_cycle(self, cycle_id: str) -> list[LedgerEntry]:
        return [entry for entry in self.entries if entry.cycle_id == cycle_id]
