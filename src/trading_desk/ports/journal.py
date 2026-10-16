"""What we need to record what happened.

The journal is the project's evidence base. Every idea the system produces, every
decision the user makes, every outcome, and - the most valuable field in the
schema - the user's reason for overriding.

Append-only by contract. A correction is a new row, never an edit, because a
journal you can rewrite proves nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from trading_desk.domain.values import Money


@dataclass(frozen=True)
class RecordedIdea:
    """An idea as the briefing presented it, with every gate value it was judged on."""

    idea_id: UUID
    cycle_id: UUID
    symbol: str
    setup_id: str
    rank: int
    confidence: float
    gate_values: dict[str, str]
    hypothetical_risk: Money
    created_at: datetime


@dataclass(frozen=True)
class RecordedDecision:
    """What the user did about an idea, and why.

    `reason` is free text on purpose. It is the input to working out which rule the
    user is applying that the system does not yet know about.
    """

    idea_id: UUID
    action: str  # TOOK | SKIPPED | MODIFIED
    reason: str
    decided_at: datetime


@dataclass(frozen=True)
class RecordedOutcome:
    """How an idea resolved. Recorded for skipped ideas too.

    Scoring only the taken ideas would measure the user's taste rather than the
    system's research, and would never reveal that the skips were the good calls.
    """

    idea_id: UUID
    resolution: str  # TARGET | STOP | TIME_STOP | INVALIDATED
    hypothetical_pnl: Money
    actual_pnl: Money | None  # None when the user did not take it
    resolved_at: datetime


class JournalPort(Protocol):
    def record_idea(self, idea: RecordedIdea) -> None: ...

    def record_decision(self, decision: RecordedDecision) -> None: ...

    def record_outcome(self, outcome: RecordedOutcome) -> None: ...

    def ideas_awaiting_decision(self, as_of: datetime) -> list[RecordedIdea]:
        """Ideas with no decision yet. Drives the 10:00 nag."""
        ...

    def resolved_ideas(self, limit: int) -> list[RecordedOutcome]:
        """Most recent resolved outcomes, for the calibration tracker."""
        ...
