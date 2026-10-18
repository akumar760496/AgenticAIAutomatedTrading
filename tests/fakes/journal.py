"""An in-memory journal with the same append-only discipline as the real one."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from trading_desk.ports.journal import (
    CalibrationSnapshot,
    RecordedDecision,
    RecordedIdea,
    RecordedOutcome,
    RecordedOverride,
)


class FakeJournal:
    """Appends, never overwrites, so a test catches a bad write without Postgres.

    A second decision or outcome for the same idea is a **correction and is
    allowed** - the lists keep both and reads take the latest. Only a repeated
    `idea_id` on `record_idea` is an error, because a briefed idea is a fact
    that happened once.
    """

    def __init__(self) -> None:
        self.ideas: list[RecordedIdea] = []
        self.decisions: list[RecordedDecision] = []
        self.outcomes: list[RecordedOutcome] = []
        self.overrides: list[RecordedOverride] = []
        self.calibrations: list[CalibrationSnapshot] = []

    def record_idea(self, idea: RecordedIdea) -> None:
        if any(existing.idea_id == idea.idea_id for existing in self.ideas):
            raise ValueError(f"Idea {idea.idea_id} already recorded - the journal is append-only")
        self.ideas.append(idea)

    def record_decision(self, decision: RecordedDecision) -> None:
        self.decisions.append(decision)

    def record_outcome(self, outcome: RecordedOutcome) -> None:
        self.outcomes.append(outcome)

    def record_override(self, override: RecordedOverride) -> None:
        self.overrides.append(override)

    def record_calibration(self, snapshot: CalibrationSnapshot) -> None:
        self.calibrations.append(snapshot)

    def ideas_awaiting_decision(self, as_of: datetime) -> list[RecordedIdea]:
        decided = {decision.idea_id for decision in self.decisions}
        return [
            idea for idea in self.ideas if idea.idea_id not in decided and idea.created_at <= as_of
        ]

    def latest_decision(self, idea_id: UUID) -> RecordedDecision | None:
        matching = [item for item in self.decisions if item.idea_id == idea_id]
        return matching[-1] if matching else None

    def resolved_ideas(self, limit: int) -> list[RecordedOutcome]:
        newest_first = sorted(self.outcomes, key=lambda o: o.resolved_at, reverse=True)
        return newest_first[:limit]
