"""The journal in Postgres. Insert only - no UPDATE, no DELETE, anywhere.

Unlike every other store in this package there is no ON CONFLICT clause except
on `briefing_ideas`, and that one raises rather than ignores. The reason is the
opposite of the bar store's: a repeated bar is a harmless re-read of an
immutable fact, whereas a repeated *decision* is the user changing their mind,
which is information. So decisions, outcomes and overrides simply append, and
reads take the newest row.

`recorded_at` is set here from the clock, not by the caller. It answers "when
did this reach the database", which together with `decided_at` shows how late
the entry was - and a decision typed in three days afterwards is weaker
evidence than one typed at 15:45 on the day.

The append-only rule is enforced by a database trigger (migration 0005), not by
this class. If this adapter is ever wrong, the database still refuses.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from trading_desk.domain.enums import Direction
from trading_desk.domain.values import Money
from trading_desk.infra.db import session_scope
from trading_desk.ports.clock import Clock
from trading_desk.ports.journal import (
    CalibrationSnapshot,
    RecordedDecision,
    RecordedIdea,
    RecordedOutcome,
    RecordedOverride,
)

INSERT_IDEA = text(
    """
    INSERT INTO briefing_ideas (
        idea_id, cycle_id, symbol, setup_id, direction, structure, rank,
        confidence, gate_values, hypothetical_risk_paise, invalidation,
        agent_rationale, used_iv_proxy, created_at
    ) VALUES (
        :idea_id, :cycle_id, :symbol, :setup_id, :direction, :structure, :rank,
        :confidence, cast(:gate_values as jsonb), :hypothetical_risk_paise,
        :invalidation, :agent_rationale, :used_iv_proxy, :created_at
    )
    """
)

INSERT_DECISION = text(
    """
    INSERT INTO user_decisions (
        idea_id, action, reason, entry_fill_paise, size, decided_at, recorded_at
    ) VALUES (
        :idea_id, :action, :reason, :entry_fill_paise, :size, :decided_at, :recorded_at
    )
    """
)

INSERT_OUTCOME = text(
    """
    INSERT INTO outcomes (
        idea_id, resolution, hypothetical_pnl_paise, actual_pnl_paise,
        max_adverse_excursion_paise, resolved_at, recorded_at
    ) VALUES (
        :idea_id, :resolution, :hypothetical_pnl_paise, :actual_pnl_paise,
        :max_adverse_excursion_paise, :resolved_at, :recorded_at
    )
    """
)

INSERT_OVERRIDE = text(
    """
    INSERT INTO override_log (
        idea_id, system_position, user_action, reason, what_followed, recorded_at
    ) VALUES (
        :idea_id, :system_position, :user_action, :reason, :what_followed, :recorded_at
    )
    """
)

INSERT_CALIBRATION = text(
    """
    INSERT INTO calibration_snapshots (
        as_of, scope, brier_score, baseline_brier_score, reliability_bins,
        sample_count, recorded_at
    ) VALUES (
        :as_of, :scope, :brier_score, :baseline_brier_score,
        cast(:reliability_bins as jsonb), :sample_count, :recorded_at
    )
    """
)

# Ideas with no decision row at all. NOT EXISTS rather than a LEFT JOIN because
# an idea with three correction rows must still count as decided, once.
SELECT_AWAITING = text(
    """
    SELECT idea_id, cycle_id, symbol, setup_id, direction, structure, rank,
           confidence, gate_values, hypothetical_risk_paise, invalidation,
           agent_rationale, used_iv_proxy, created_at
    FROM briefing_ideas idea
    WHERE created_at <= :as_of
      AND NOT EXISTS (
          SELECT 1 FROM user_decisions WHERE user_decisions.idea_id = idea.idea_id
      )
    ORDER BY created_at
    """
)

# The newest row wins. This is what makes a correction a correction.
SELECT_LATEST_DECISION = text(
    """
    SELECT idea_id, action, reason, entry_fill_paise, size, decided_at
    FROM user_decisions
    WHERE idea_id = :idea_id
    ORDER BY recorded_at DESC, decision_id DESC
    LIMIT 1
    """
)

SELECT_RESOLVED = text(
    """
    SELECT DISTINCT ON (idea_id)
           idea_id, resolution, hypothetical_pnl_paise, actual_pnl_paise,
           max_adverse_excursion_paise, resolved_at
    FROM outcomes
    ORDER BY idea_id, recorded_at DESC, outcome_id DESC
    """
)


class PostgresJournal:
    """Implements JournalPort against the five journal tables."""

    def __init__(self, factory: sessionmaker[Session], clock: Clock) -> None:
        self._factory = factory
        self._clock = clock

    def record_idea(self, idea: RecordedIdea) -> None:
        with session_scope(self._factory) as session:
            session.execute(
                INSERT_IDEA,
                {
                    "idea_id": idea.idea_id,
                    "cycle_id": idea.cycle_id,
                    "symbol": idea.symbol,
                    "setup_id": idea.setup_id,
                    "direction": str(idea.direction),
                    "structure": idea.structure,
                    "rank": idea.rank,
                    "confidence": idea.confidence,
                    "gate_values": _as_json(idea.gate_values),
                    "hypothetical_risk_paise": idea.hypothetical_risk.paise,
                    "invalidation": idea.invalidation,
                    "agent_rationale": idea.agent_rationale,
                    "used_iv_proxy": idea.used_iv_proxy,
                    "created_at": idea.created_at,
                },
            )

    def record_decision(self, decision: RecordedDecision) -> None:
        with session_scope(self._factory) as session:
            session.execute(
                INSERT_DECISION,
                {
                    "idea_id": decision.idea_id,
                    "action": decision.action,
                    "reason": decision.reason,
                    "entry_fill_paise": _paise_or_none(decision.entry_fill),
                    "size": decision.size,
                    "decided_at": decision.decided_at,
                    "recorded_at": self._clock.utc_now(),
                },
            )

    def record_outcome(self, outcome: RecordedOutcome) -> None:
        with session_scope(self._factory) as session:
            session.execute(
                INSERT_OUTCOME,
                {
                    "idea_id": outcome.idea_id,
                    "resolution": outcome.resolution,
                    "hypothetical_pnl_paise": outcome.hypothetical_pnl.paise,
                    "actual_pnl_paise": _paise_or_none(outcome.actual_pnl),
                    "max_adverse_excursion_paise": _paise_or_none(outcome.max_adverse_excursion),
                    "resolved_at": outcome.resolved_at,
                    "recorded_at": self._clock.utc_now(),
                },
            )

    def record_override(self, override: RecordedOverride) -> None:
        with session_scope(self._factory) as session:
            session.execute(
                INSERT_OVERRIDE,
                {
                    "idea_id": override.idea_id,
                    "system_position": override.system_position,
                    "user_action": override.user_action,
                    "reason": override.reason,
                    "what_followed": override.what_followed,
                    "recorded_at": override.recorded_at,
                },
            )

    def record_calibration(self, snapshot: CalibrationSnapshot) -> None:
        with session_scope(self._factory) as session:
            session.execute(
                INSERT_CALIBRATION,
                {
                    "as_of": snapshot.as_of,
                    "scope": snapshot.scope,
                    "brier_score": snapshot.brier_score,
                    "baseline_brier_score": snapshot.baseline_brier_score,
                    "reliability_bins": _as_json(snapshot.reliability_bins),
                    "sample_count": snapshot.sample_count,
                    "recorded_at": self._clock.utc_now(),
                },
            )

    def ideas_awaiting_decision(self, as_of: datetime) -> list[RecordedIdea]:
        with session_scope(self._factory) as session:
            rows = session.execute(SELECT_AWAITING, {"as_of": as_of}).mappings().all()
        return [_to_idea(row) for row in rows]

    def latest_decision(self, idea_id: UUID) -> RecordedDecision | None:
        with session_scope(self._factory) as session:
            row = session.execute(SELECT_LATEST_DECISION, {"idea_id": idea_id}).mappings().first()
        if row is None:
            return None
        return RecordedDecision(
            idea_id=row["idea_id"],
            action=row["action"],
            reason=row["reason"],
            decided_at=row["decided_at"],
            entry_fill=_money_or_none(row["entry_fill_paise"]),
            size=row["size"],
        )

    def resolved_ideas(self, limit: int) -> list[RecordedOutcome]:
        with session_scope(self._factory) as session:
            rows = session.execute(SELECT_RESOLVED).mappings().all()

        outcomes = [_to_outcome(row) for row in rows]
        outcomes.sort(key=lambda item: item.resolved_at, reverse=True)
        return outcomes[:limit]


def _as_json(values: dict[str, str]) -> str:
    return json.dumps(values)


def _paise_or_none(amount: Money | None) -> int | None:
    return None if amount is None else amount.paise


def _money_or_none(paise: int | None) -> Money | None:
    return None if paise is None else Money(paise=int(paise))


def _to_idea(row: Any) -> RecordedIdea:
    return RecordedIdea(
        idea_id=row["idea_id"],
        cycle_id=row["cycle_id"],
        symbol=row["symbol"],
        setup_id=row["setup_id"],
        direction=Direction(row["direction"]),
        rank=row["rank"],
        confidence=float(row["confidence"]),
        gate_values=dict(row["gate_values"]),
        hypothetical_risk=Money(paise=int(row["hypothetical_risk_paise"])),
        created_at=row["created_at"],
        structure=row["structure"],
        invalidation=row["invalidation"],
        agent_rationale=row["agent_rationale"],
        used_iv_proxy=row["used_iv_proxy"],
    )


def _to_outcome(row: Any) -> RecordedOutcome:
    return RecordedOutcome(
        idea_id=row["idea_id"],
        resolution=row["resolution"],
        hypothetical_pnl=Money(paise=int(row["hypothetical_pnl_paise"])),
        actual_pnl=_money_or_none(row["actual_pnl_paise"]),
        resolved_at=row["resolved_at"],
        max_adverse_excursion=_money_or_none(row["max_adverse_excursion_paise"]),
    )
