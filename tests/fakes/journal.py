"""An in-memory journal that enforces append-only, like the real one will."""

from __future__ import annotations

from datetime import datetime

from trading_desk.ports.journal import RecordedDecision, RecordedIdea, RecordedOutcome


class FakeJournal:
    """Stores records in lists and refuses to overwrite.

    The append-only rule is enforced here, not just in the database, so a test
    catches a double-write without needing Postgres running.
    """

    def __init__(self) -> None:
        self.ideas: list[RecordedIdea] = []
        self.decisions: list[RecordedDecision] = []
        self.outcomes: list[RecordedOutcome] = []

    def record_idea(self, idea: RecordedIdea) -> None:
        if any(existing.idea_id == idea.idea_id for existing in self.ideas):
            raise ValueError(f"Idea {idea.idea_id} already recorded - the journal is append-only")
        self.ideas.append(idea)

    def record_decision(self, decision: RecordedDecision) -> None:
        if any(existing.idea_id == decision.idea_id for existing in self.decisions):
            raise ValueError(
                f"Decision for {decision.idea_id} already recorded - "
                "record a correction as a new row instead"
            )
        self.decisions.append(decision)

    def record_outcome(self, outcome: RecordedOutcome) -> None:
        if any(existing.idea_id == outcome.idea_id for existing in self.outcomes):
            raise ValueError(f"Outcome for {outcome.idea_id} already recorded")
        self.outcomes.append(outcome)

    def ideas_awaiting_decision(self, as_of: datetime) -> list[RecordedIdea]:
        decided = {decision.idea_id for decision in self.decisions}
        return [
            idea for idea in self.ideas if idea.idea_id not in decided and idea.created_at <= as_of
        ]

    def resolved_ideas(self, limit: int) -> list[RecordedOutcome]:
        newest_first = sorted(self.outcomes, key=lambda o: o.resolved_at, reverse=True)
        return newest_first[:limit]
