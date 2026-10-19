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
from collections.abc import Sequence
from dataclasses import replace
from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from trading_desk.domain.enums import Direction, OptionRight
from trading_desk.domain.proposals import LegAction
from trading_desk.domain.values import Money, Price
from trading_desk.infra.db import session_scope
from trading_desk.ports.clock import Clock
from trading_desk.ports.journal import (
    CalibrationSnapshot,
    RecordedDecision,
    RecordedIdea,
    RecordedLeg,
    RecordedOutcome,
    RecordedOverride,
    ResolvedScore,
)

INSERT_IDEA = text(
    """
    INSERT INTO briefing_ideas (
        idea_id, cycle_id, symbol, setup_id, direction, structure, rank,
        confidence, gate_values, hypothetical_risk_paise, invalidation,
        agent_rationale, used_iv_proxy, created_at,
        stop_level_paise, target_level_paise, expiry_date
    ) VALUES (
        :idea_id, :cycle_id, :symbol, :setup_id, :direction, :structure, :rank,
        :confidence, cast(:gate_values as jsonb), :hypothetical_risk_paise,
        :invalidation, :agent_rationale, :used_iv_proxy, :created_at,
        :stop_level_paise, :target_level_paise, :expiry_date
    )
    """
)

INSERT_LEG = text(
    """
    INSERT INTO briefing_idea_legs (
        idea_id, leg_index, option_right, action, strike_paise, expiry_date,
        entry_premium_paise, recorded_at
    ) VALUES (
        :idea_id, :leg_index, :option_right, :action, :strike_paise, :expiry_date,
        :entry_premium_paise, :recorded_at
    )
    """
)

# Legs for a set of ideas, in briefing order. One query for the whole page
# rather than one per idea: a vertical has two legs and a page has fifty ideas,
# which is a hundred round trips to avoid.
SELECT_LEGS = text(
    """
    SELECT idea_id, leg_index, option_right, action, strike_paise, expiry_date,
           entry_premium_paise
    FROM briefing_idea_legs
    WHERE idea_id = ANY(:idea_ids)
    ORDER BY idea_id, leg_index
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
        max_adverse_excursion_paise, resolved_at, recorded_at,
        basis, gross_pnl_paise, costs_paise, measured, threshold
    ) VALUES (
        :idea_id, :resolution, :hypothetical_pnl_paise, :actual_pnl_paise,
        :max_adverse_excursion_paise, :resolved_at, :recorded_at,
        :basis, :gross_pnl_paise, :costs_paise, :measured, :threshold
    )
    """
)

# Each idea's latest outcome, joined to the confidence that was claimed for it.
# DISTINCT ON keeps the newest correction, exactly as SELECT_RESOLVED does - a
# second outcome row for an idea is a correction, not a second trade.
SELECT_RESOLVED_SCORES = text(
    """
    SELECT DISTINCT ON (o.idea_id)
           o.idea_id, o.resolution, o.resolved_at,
           o.hypothetical_pnl_paise, o.gross_pnl_paise, o.costs_paise,
           i.setup_id, i.confidence, i.used_iv_proxy
    FROM outcomes o
    JOIN briefing_ideas i ON i.idea_id = o.idea_id
    ORDER BY o.idea_id, o.recorded_at DESC, o.outcome_id DESC
    """
)

# Distinct IST dates with at least one briefed idea. The AT TIME ZONE cast is
# the point: created_at is UTC, and a 20:00 UTC briefing is the next IST day.
# Grouping on the UTC date would put some evenings in the wrong session.
SELECT_BRIEFED_DATES = text(
    """
    SELECT DISTINCT (created_at AT TIME ZONE 'Asia/Kolkata')::date AS ist_date
    FROM briefing_ideas
    WHERE created_at >= :since
    ORDER BY ist_date
    """
)

# Briefed, and how many of those have any decision row. One query so the two
# numbers cannot be read a moment apart and disagree.
SELECT_ENGAGEMENT = text(
    """
    SELECT count(*) AS briefed,
           count(*) FILTER (
               WHERE EXISTS (
                   SELECT 1 FROM user_decisions d WHERE d.idea_id = i.idea_id
               )
           ) AS answered
    FROM briefing_ideas i
    WHERE created_at >= :since
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
           agent_rationale, used_iv_proxy, created_at,
           stop_level_paise, target_level_paise, expiry_date
    FROM briefing_ideas idea
    WHERE created_at <= :as_of
      AND NOT EXISTS (
          SELECT 1 FROM user_decisions WHERE user_decisions.idea_id = idea.idea_id
      )
    ORDER BY created_at
    """
)

# Still running: briefed, and no outcome recorded. A *decided* idea is
# included on purpose - a taken trade is the one the review most needs.
SELECT_WITHOUT_OUTCOME = text(
    """
    SELECT idea_id, cycle_id, symbol, setup_id, direction, structure, rank,
           confidence, gate_values, hypothetical_risk_paise, invalidation,
           agent_rationale, used_iv_proxy, created_at,
           stop_level_paise, target_level_paise, expiry_date
    FROM briefing_ideas idea
    WHERE created_at >= :since
      AND NOT EXISTS (
          SELECT 1 FROM outcomes WHERE outcomes.idea_id = idea.idea_id
      )
    ORDER BY created_at
    LIMIT :limit
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
           max_adverse_excursion_paise, resolved_at,
           basis, gross_pnl_paise, costs_paise, measured, threshold
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
                    "stop_level_paise": _paise_of(idea.stop_level),
                    "target_level_paise": _paise_of(idea.target_level),
                    "expiry_date": idea.expiry,
                },
            )
            # Same transaction as the idea. An idea whose legs failed to write
            # would be silently unscoreable for ever, and the whole point of
            # 0009 is that the legs exist.
            recorded_at = self._clock.utc_now()
            for index, leg in enumerate(idea.legs):
                session.execute(
                    INSERT_LEG,
                    {
                        "idea_id": idea.idea_id,
                        "leg_index": index,
                        "option_right": str(leg.right),
                        "action": str(leg.action),
                        "strike_paise": leg.strike.paise,
                        "expiry_date": leg.expiry,
                        "entry_premium_paise": leg.entry_premium.paise,
                        "recorded_at": recorded_at,
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
                    "basis": outcome.basis,
                    "gross_pnl_paise": _paise_or_none(outcome.gross_pnl),
                    "costs_paise": _paise_or_none(outcome.costs),
                    "measured": outcome.measured,
                    "threshold": outcome.threshold,
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
            return self._with_legs(session, rows)

    def ideas_without_outcome(self, since: date, limit: int) -> list[RecordedIdea]:
        with session_scope(self._factory) as session:
            rows = (
                session.execute(SELECT_WITHOUT_OUTCOME, {"since": since, "limit": limit})
                .mappings()
                .all()
            )
            return self._with_legs(session, rows)

    def _with_legs(self, session: Session, rows: Sequence[Any]) -> list[RecordedIdea]:
        """Attach each idea's legs. One extra query for the whole page."""
        ideas = [_to_idea(row) for row in rows]
        if not ideas:
            return []

        legs: dict[UUID, list[RecordedLeg]] = {}
        leg_rows = (
            session.execute(SELECT_LEGS, {"idea_ids": [idea.idea_id for idea in ideas]})
            .mappings()
            .all()
        )
        for row in leg_rows:
            legs.setdefault(row["idea_id"], []).append(_to_leg(row))

        # An idea with no leg rows keeps its empty tuple rather than being
        # dropped: it is a real idea that predates 0009, and the scoring cycle
        # reports it as unscoreable instead of pretending it does not exist.
        return [replace(idea, legs=tuple(legs.get(idea.idea_id, []))) for idea in ideas]

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

    def briefed_dates(self, since: date) -> list[date]:
        with session_scope(self._factory) as session:
            rows = session.execute(SELECT_BRIEFED_DATES, {"since": since}).all()
        return [row[0] for row in rows]

    def engagement_counts(self, since: date) -> tuple[int, int]:
        with session_scope(self._factory) as session:
            row = session.execute(SELECT_ENGAGEMENT, {"since": since}).mappings().one()
        return int(row["briefed"]), int(row["answered"])

    def resolved_scores(self, limit: int) -> list[ResolvedScore]:
        with session_scope(self._factory) as session:
            rows = session.execute(SELECT_RESOLVED_SCORES).mappings().all()

        scores = [
            ResolvedScore(
                idea_id=row["idea_id"],
                setup_id=row["setup_id"],
                confidence=float(row["confidence"]),
                resolution=row["resolution"],
                net_pnl=Money(paise=int(row["hypothetical_pnl_paise"])),
                resolved_at=row["resolved_at"],
                used_iv_proxy=bool(row["used_iv_proxy"]),
            )
            for row in rows
        ]
        scores.sort(key=lambda item: item.resolved_at, reverse=True)
        return scores[:limit]


def _as_json(values: dict[str, str]) -> str:
    return json.dumps(values)


def _paise_or_none(amount: Money | None) -> int | None:
    return None if amount is None else amount.paise


def _money_or_none(paise: int | None) -> Money | None:
    return None if paise is None else Money(paise=int(paise))


def _price_or_none(paise: int | None) -> Price | None:
    return None if paise is None else Price(paise=int(paise))


def _paise_of(level: Price | None) -> int | None:
    return None if level is None else level.paise


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
        stop_level=_price_or_none(row["stop_level_paise"]),
        target_level=_price_or_none(row["target_level_paise"]),
        expiry=row["expiry_date"],
    )


def _to_leg(row: Any) -> RecordedLeg:
    return RecordedLeg(
        right=OptionRight(row["option_right"]),
        action=LegAction(row["action"]),
        strike=Price(paise=int(row["strike_paise"])),
        expiry=row["expiry_date"],
        entry_premium=Money(paise=int(row["entry_premium_paise"])),
    )


def _to_outcome(row: Any) -> RecordedOutcome:
    return RecordedOutcome(
        idea_id=row["idea_id"],
        resolution=row["resolution"],
        hypothetical_pnl=Money(paise=int(row["hypothetical_pnl_paise"])),
        actual_pnl=_money_or_none(row["actual_pnl_paise"]),
        resolved_at=row["resolved_at"],
        basis=row["basis"],
        gross_pnl=_money_or_none(row["gross_pnl_paise"]),
        costs=_money_or_none(row["costs_paise"]),
        measured=row["measured"],
        threshold=row["threshold"],
        max_adverse_excursion=_money_or_none(row["max_adverse_excursion_paise"]),
    )
