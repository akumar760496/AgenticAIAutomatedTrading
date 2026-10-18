"""What we need to record what happened.

The journal is the project's evidence base. Every idea the system produces,
every decision the user makes, every outcome, and - the most valuable field in
the schema - the user's reason for overriding.

**Append-only, and a correction is a new row.** This is the one place the
contract reads oddly at first: `record_decision` called twice for the same idea
is *not* an error. Changing your mind is expected, and the history of changing
it is itself evidence - "I skipped it, then took it an hour later" says
something that an overwritten row destroys. Reads return the latest row. The
rule the adapter must never break is that nothing is ever UPDATEd or DELETEd,
and the database enforces that with a trigger rather than trusting the adapter.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol
from uuid import UUID

from trading_desk.domain.enums import Direction
from trading_desk.domain.values import Money


@dataclass(frozen=True)
class RecordedIdea:
    """An idea as the briefing presented it, with every gate value it was judged on."""

    idea_id: UUID
    cycle_id: UUID
    symbol: str
    setup_id: str
    direction: Direction
    rank: int
    confidence: float
    # Every gate's *measured value*, not its verdict. Storing PASS/FAIL discards
    # the number, and the number is what answers "which gate actually predicted
    # anything?" a year from now (CLAUDE.md rule 9).
    gate_values: dict[str, str]
    hypothetical_risk: Money
    created_at: datetime
    structure: str = ""
    invalidation: str = ""
    agent_rationale: str = ""
    # True when iv_rank came from the realised-vol proxy rather than real IV
    # history. Without it, months of proxy-based ideas are indistinguishable
    # from real ones in the calibration maths.
    used_iv_proxy: bool = False


@dataclass(frozen=True)
class RecordedDecision:
    """What the user did about an idea, and why.

    `reason` is free text on purpose. It is the input to working out which rule
    the user is applying that the system does not yet know about.
    """

    idea_id: UUID
    action: str  # TOOK | SKIPPED | MODIFIED
    reason: str
    decided_at: datetime
    # Both None unless the idea was taken. Size is the user's own number: an LLM
    # never sizes a position (rule 1). It is recorded after the fact so outcomes
    # can be scored, never to drive an order.
    entry_fill: Money | None = None
    size: int | None = None

    def __post_init__(self) -> None:
        if not self.reason.strip():
            raise ValueError(f"{self.idea_id}: a decision must carry a reason")


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
    # How far it went against you before it worked. A winner that first drew
    # down three times the stop is not a repeatable idea.
    max_adverse_excursion: Money | None = None


@dataclass(frozen=True)
class RecordedOverride:
    """A disagreement between the user and the system.

    `what_followed` is optional because the disagreement is worth recording
    before the result is known. Waiting for the result is how override logs end
    up empty.
    """

    idea_id: UUID
    system_position: str
    user_action: str
    reason: str
    recorded_at: datetime
    what_followed: str | None = None


@dataclass(frozen=True)
class CalibrationSnapshot:
    """Whether the confidence numbers mean anything, measured not assumed."""

    as_of: datetime
    scope: str  # an agent name, a setup id, or ALL
    brier_score: float
    # Always predicting the base rate. Without it a Brier score is a number
    # with no sense of whether the model beat guessing.
    baseline_brier_score: float
    sample_count: int
    reliability_bins: dict[str, str] = field(default_factory=dict)


class JournalPort(Protocol):
    def record_idea(self, idea: RecordedIdea) -> None:
        """Append one briefed idea. The same idea_id twice is an error."""
        ...

    def record_decision(self, decision: RecordedDecision) -> None:
        """Append a decision. A second one for the same idea is a correction."""
        ...

    def record_outcome(self, outcome: RecordedOutcome) -> None:
        """Append an outcome. A second one for the same idea is a correction."""
        ...

    def record_override(self, override: RecordedOverride) -> None: ...

    def record_calibration(self, snapshot: CalibrationSnapshot) -> None: ...

    def ideas_awaiting_decision(self, as_of: datetime) -> list[RecordedIdea]:
        """Ideas with no decision yet. Drives the 10:00 nag."""
        ...

    def latest_decision(self, idea_id: UUID) -> RecordedDecision | None:
        """The current decision for an idea - the newest row, corrections included."""
        ...

    def resolved_ideas(self, limit: int) -> list[RecordedOutcome]:
        """Most recent resolved outcomes, for the calibration tracker."""
        ...
