"""Every model call we have ever paid for.

Separate from `LlmPort` on purpose. The model is a thing that answers questions;
the ledger is a thing that remembers what we spent. Folding the second into the
first would mean the in-memory fake also decides the budget, and a cap that
resets whenever the process restarts is not a monthly cap.

Append-only, like the journal: a spend record is evidence of what happened, and
a ledger that can be edited cannot answer "did we stay under the cap in March?"
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from trading_desk.domain.llm_cost import ModelTier
from trading_desk.domain.values import Money


@dataclass(frozen=True)
class LedgerEntry:
    """One paid call.

    `cycle_id` and `node` are what make the per-node cost report in the briefing
    possible. Without them the ledger can only answer "how much this month",
    which is the question you ask after the bill, not before.
    """

    cycle_id: str
    node: str
    tier: ModelTier
    model: str
    input_tokens: int
    output_tokens: int
    cost: Money
    called_at: datetime


class LlmLedger(Protocol):
    """Where spend is recorded and read back."""

    def record(self, entry: LedgerEntry) -> None:
        """Append one call. Never updates an existing row."""
        ...

    def spend_in_month(self, year: int, month: int) -> Money:
        """Total spend in one calendar month, in IST - which is the month Ajay
        thinks in. Anthropic bills in UTC, so the two differ for calls made in
        the last 5.5 hours of a month; the gap is reconciled against the console
        rather than papered over here."""
        ...

    def entries_for_cycle(self, cycle_id: str) -> list[LedgerEntry]:
        """Every call made by one research cycle, for the per-node report."""
        ...
