"""An in-memory journal with the same append-only discipline as the real one."""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from trading_desk.domain.timezones import to_ist
from trading_desk.ports.journal import (
    CalibrationSnapshot,
    RecordedDecision,
    RecordedIdea,
    RecordedOutcome,
    RecordedOverride,
    ResolvedScore,
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

    def ideas_without_outcome(self, since: date, limit: int) -> list[RecordedIdea]:
        resolved = {outcome.idea_id for outcome in self.outcomes}
        open_ideas = [
            idea
            for idea in self.ideas
            if idea.idea_id not in resolved and idea.created_at.date() >= since
        ]
        open_ideas.sort(key=lambda idea: idea.created_at)
        return open_ideas[:limit]

    def latest_decision(self, idea_id: UUID) -> RecordedDecision | None:
        matching = [item for item in self.decisions if item.idea_id == idea_id]
        return matching[-1] if matching else None

    def resolved_ideas(self, limit: int) -> list[RecordedOutcome]:
        newest_first = sorted(self.outcomes, key=lambda o: o.resolved_at, reverse=True)
        return newest_first[:limit]

    def briefed_dates(self, since: date) -> list[date]:
        """IST dates with at least one idea. Sorted and deduplicated."""
        days = {
            to_ist(idea.created_at).date()
            for idea in self.ideas
            if to_ist(idea.created_at).date() >= since
        }
        return sorted(days)

    def engagement_counts(self, since: date) -> tuple[int, int]:
        decided = {decision.idea_id for decision in self.decisions}
        briefed = [idea for idea in self.ideas if to_ist(idea.created_at).date() >= since]
        return len(briefed), sum(1 for idea in briefed if idea.idea_id in decided)

    def resolved_scores(self, limit: int) -> list[ResolvedScore]:
        """Outcomes joined to the confidence that was claimed for them.

        The join is done here rather than stored, so a test that records an
        outcome for an idea it never recorded gets a KeyError instead of a
        silently absent row. A missing idea is a bug in the test or the code,
        never a reason to drop evidence.
        """
        by_id = {idea.idea_id: idea for idea in self.ideas}
        latest: dict[UUID, RecordedOutcome] = {}
        for outcome in self.outcomes:
            latest[outcome.idea_id] = outcome  # a later row is a correction

        scores = [
            ResolvedScore(
                idea_id=outcome.idea_id,
                setup_id=by_id[outcome.idea_id].setup_id,
                confidence=by_id[outcome.idea_id].confidence,
                resolution=outcome.resolution,
                net_pnl=outcome.hypothetical_pnl,
                resolved_at=outcome.resolved_at,
                used_iv_proxy=by_id[outcome.idea_id].used_iv_proxy,
            )
            for outcome in latest.values()
        ]
        scores.sort(key=lambda item: item.resolved_at, reverse=True)
        return scores[:limit]
