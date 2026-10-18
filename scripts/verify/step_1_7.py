"""Verify step 1.7 - the journal schema.

The step is "done when a fake briefing writes a full round trip: idea ->
decision -> outcome -> override reason, and the export reads back identically."
That round trip is the first four checks.

The rest prove the discipline rather than the plumbing, because this table is
only evidence if it cannot be quietly rewritten:

- UPDATE, DELETE **and TRUNCATE** are refused by the database, not by the
  adapter. TRUNCATE needs saying separately: it does not fire row-level
  triggers and emptied the table cleanly until it got one of its own.
- A decision with a blank reason is refused. docs/06 calls the reason the most
  valuable field in the schema, and a nullable column collects nulls.
- A second decision for one idea is a **correction, not an error**, and the
  later row wins. An overwritten decision destroys the fact that the user
  changed their mind, which is itself the signal.

Every row this script writes is removed afterwards by dropping and re-applying
migration 0005, because the triggers mean nothing can be deleted - which is the
point, and is also why the cleanup looks heavy-handed.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID, uuid4

import polars as pl
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from scripts.verify._runner import CheckResult
from trading_desk.adapters.clock import SystemClock
from trading_desk.adapters.store.journal import PostgresJournal
from trading_desk.application.journal_export import JOURNAL_TABLES, JournalExport
from trading_desk.domain.enums import Direction
from trading_desk.domain.values import Money
from trading_desk.infra.config import load_infra_settings
from trading_desk.infra.db import create_db_engine, create_session_factory
from trading_desk.ports.journal import (
    CalibrationSnapshot,
    RecordedDecision,
    RecordedIdea,
    RecordedOutcome,
    RecordedOverride,
)

TITLE = "Journal schema"

NOW = datetime.now(UTC)
EXPECTED_TABLES = 5
HYPOTHETICAL_RISK = Money.from_rupees(Decimal("2500"))
HYPOTHETICAL_PNL = Money.from_rupees(Decimal("4120.50"))


def _engine() -> Engine:
    return create_db_engine(load_infra_settings())


def _journal() -> PostgresJournal:
    return PostgresJournal(create_session_factory(_engine()), SystemClock())


def _an_idea(cycle_id: UUID, rank: int = 1) -> RecordedIdea:
    return RecordedIdea(
        idea_id=uuid4(),
        cycle_id=cycle_id,
        symbol="RELIANCE",
        setup_id="SETUP_A",
        direction=Direction.BULLISH,
        rank=rank,
        confidence=0.62,
        gate_values={"iv_rank": "42.0", "atr_paise": "88900", "credibility": "0.81"},
        hypothetical_risk=HYPOTHETICAL_RISK,
        created_at=NOW,
        structure="long 1400 call, 2 weeks",
        invalidation="close below 1380",
        agent_rationale="demerger filing plus volume expansion",
        used_iv_proxy=True,
    )


def _round_trip() -> tuple[bool, str, RecordedIdea]:
    """idea -> decision -> outcome -> override, read back identically."""
    journal = _journal()
    idea = _an_idea(uuid4())

    journal.record_idea(idea)
    journal.record_decision(
        RecordedDecision(
            idea_id=idea.idea_id,
            action="TOOK",
            reason="filing was unambiguous and the spread was 0.4%",
            decided_at=NOW,
            entry_fill=Money.from_rupees(Decimal("1412.35")),
            size=2,
        )
    )
    journal.record_outcome(
        RecordedOutcome(
            idea_id=idea.idea_id,
            resolution="TARGET",
            hypothetical_pnl=HYPOTHETICAL_PNL,
            actual_pnl=Money.from_rupees(Decimal("3980")),
            resolved_at=NOW + timedelta(days=5),
            max_adverse_excursion=Money.from_rupees(Decimal("-1120")),
        )
    )
    journal.record_override(
        RecordedOverride(
            idea_id=idea.idea_id,
            system_position="rank 2, confidence 0.62",
            user_action="took it ahead of rank 1",
            reason="rank 1 had earnings in two days and I do not hold through prints",
            recorded_at=NOW,
            what_followed="rank 1 gapped down 6% on the print",
        )
    )
    journal.record_calibration(
        CalibrationSnapshot(
            as_of=NOW,
            scope="ALL",
            brier_score=0.21,
            baseline_brier_score=0.25,
            sample_count=1,
            reliability_bins={"0.6-0.7": "1/1"},
        )
    )

    decision = journal.latest_decision(idea.idea_id)
    outcomes = journal.resolved_ideas(10)
    mine = [item for item in outcomes if item.idea_id == idea.idea_id]

    if decision is None:
        return False, "decision did not read back", idea
    if not mine:
        return False, "outcome did not read back", idea

    # Compared as Money, so a float creeping in anywhere would show here.
    identical = (
        decision.action == "TOOK"
        and decision.entry_fill == Money.from_rupees(Decimal("1412.35"))
        and mine[0].hypothetical_pnl == HYPOTHETICAL_PNL
        and mine[0].max_adverse_excursion == Money.from_rupees(Decimal("-1120"))
    )
    return (
        identical,
        (
            f"idea -> decision ({decision.action}, fill "
            f"₹{decision.entry_fill.rupees() if decision.entry_fill else 0}) -> "
            f"outcome ({mine[0].resolution}, hypothetical ₹{mine[0].hypothetical_pnl.rupees()}) "
            f"-> override, all read back"
        ),
        idea,
    )


def _gate_values_survive(idea: RecordedIdea) -> tuple[bool, str]:
    """Every gate's measured value, not just its verdict (rule 9)."""
    with _engine().connect() as connection:
        stored = connection.execute(
            text("SELECT gate_values FROM briefing_ideas WHERE idea_id = :id"),
            {"id": idea.idea_id},
        ).scalar_one()
    return dict(stored) == idea.gate_values, f"{len(stored)} gate values stored verbatim: {stored}"


def _rejected_by_trigger(error: DBAPIError, statement: str) -> tuple[bool, str]:
    """Pass only if the *append-only trigger* refused it, not something else.

    Found by inducing: with the triggers dropped, DELETE was still refused - by
    a foreign key from user_decisions - and the old wording reported it as
    "rejected by the database trigger". The check would have gone on passing
    with no append-only protection at all.
    """
    if "append-only" in str(error):
        return True, f"{statement} rejected by the append-only trigger"
    first_line = str(error).strip().splitlines()[0][:90]
    return False, f"{statement} refused, but not by the trigger: {first_line}"


def _update_is_refused(idea: RecordedIdea) -> tuple[bool, str]:
    with _engine().begin() as connection:
        try:
            connection.execute(
                text("UPDATE briefing_ideas SET confidence = 0.99 WHERE idea_id = :id"),
                {"id": idea.idea_id},
            )
        except DBAPIError as error:
            return _rejected_by_trigger(error, "UPDATE")
    return False, "UPDATE SUCCEEDED - the journal can be rewritten"


def _delete_is_refused(idea: RecordedIdea) -> tuple[bool, str]:
    with _engine().begin() as connection:
        try:
            connection.execute(
                text("DELETE FROM briefing_ideas WHERE idea_id = :id"), {"id": idea.idea_id}
            )
        except DBAPIError as error:
            return _rejected_by_trigger(error, "DELETE")
    return False, "DELETE SUCCEEDED - the journal can be erased"


def _truncate_is_refused() -> tuple[bool, str]:
    """Separate from DELETE: TRUNCATE does not fire row-level triggers."""
    with _engine().begin() as connection:
        try:
            connection.execute(text("TRUNCATE briefing_ideas CASCADE"))
        except DBAPIError as error:
            return _rejected_by_trigger(error, "TRUNCATE")
    return False, "TRUNCATE SUCCEEDED - the whole journal can be wiped in one statement"


def _blank_reason_is_refused(idea: RecordedIdea) -> tuple[bool, str]:
    """The constraint, not the dataclass - the database must refuse it too."""
    with _engine().begin() as connection:
        try:
            connection.execute(
                text(
                    "INSERT INTO user_decisions (idea_id, action, reason, decided_at, recorded_at)"
                    " VALUES (:id, 'SKIPPED', '   ', now(), now())"
                ),
                {"id": idea.idea_id},
            )
        except IntegrityError as error:
            return "reason_present" in str(error), "blank reason rejected by CHECK constraint"
    return False, "a decision with no reason was accepted"


def _a_correction_is_a_new_row(idea: RecordedIdea) -> tuple[bool, str]:
    journal = _journal()
    journal.record_decision(
        RecordedDecision(
            idea_id=idea.idea_id,
            action="MODIFIED",
            reason="halved the size after re-reading the filing",
            decided_at=NOW + timedelta(hours=1),
        )
    )
    with _engine().connect() as connection:
        count = connection.execute(
            text("SELECT count(*) FROM user_decisions WHERE idea_id = :id"), {"id": idea.idea_id}
        ).scalar_one()

    latest = journal.latest_decision(idea.idea_id)
    correct = int(count) == 2 and latest is not None and latest.action == "MODIFIED"  # noqa: PLR2004
    return correct, f"{count} decision rows kept, latest reads {latest.action if latest else '-'}"


def _export_reads_back_identically() -> tuple[bool, str]:
    with TemporaryDirectory() as temp:
        export = JournalExport(_engine(), SystemClock(), Path(temp))
        report = export.run()

        written = {table.name for table in report.tables}
        if written != set(JOURNAL_TABLES):
            return False, f"exported {sorted(written)}, expected {sorted(JOURNAL_TABLES)}"

        ideas = next(table for table in report.tables if table.name == "briefing_ideas")
        frame = pl.read_parquet(ideas.path)

        with _engine().connect() as connection:
            in_db = connection.execute(text("SELECT count(*) FROM briefing_ideas")).scalar_one()

        matches = frame.height == int(in_db)
        return matches, (
            f"{len(report.tables)} tables to Parquet, {report.total_rows} rows; "
            f"briefing_ideas {frame.height} in file vs {in_db} in DB"
        )


def _reset_journal_tables() -> None:
    """Drop and re-apply 0005. The triggers mean nothing can be deleted."""
    config = Config("alembic.ini")
    command.downgrade(config, "0004")
    command.upgrade(config, "head")


def run() -> list[CheckResult]:
    round_trip_ok, round_trip_detail, idea = _round_trip()
    gates_ok, gates_detail = _gate_values_survive(idea)
    update_ok, update_detail = _update_is_refused(idea)
    delete_ok, delete_detail = _delete_is_refused(idea)
    truncate_ok, truncate_detail = _truncate_is_refused()
    blank_ok, blank_detail = _blank_reason_is_refused(idea)
    correction_ok, correction_detail = _a_correction_is_a_new_row(idea)
    export_ok, export_detail = _export_reads_back_identically()

    _reset_journal_tables()

    return [
        CheckResult(
            name="full_round_trip_reads_back",
            passed=round_trip_ok,
            measured=round_trip_detail,
            requirement="need idea -> decision -> outcome -> override stored and identical",
        ),
        CheckResult(
            name="every_gate_value_is_stored_not_just_the_verdict",
            passed=gates_ok,
            measured=gates_detail,
            requirement="need the measured numbers kept, so gates can be scored later",
        ),
        CheckResult(
            name="update_is_refused_by_the_database",
            passed=update_ok,
            measured=update_detail,
            requirement="need the trigger to reject it - an adapter promise is not evidence",
        ),
        CheckResult(
            name="delete_is_refused_by_the_database",
            passed=delete_ok,
            measured=delete_detail,
            requirement="need the trigger to reject it",
        ),
        CheckResult(
            name="truncate_is_refused_by_the_database",
            passed=truncate_ok,
            measured=truncate_detail,
            requirement="need a statement trigger - TRUNCATE skips row-level ones",
        ),
        CheckResult(
            name="a_decision_without_a_reason_is_refused",
            passed=blank_ok,
            measured=blank_detail,
            requirement="need the CHECK to reject it - the reason is the point of the table",
        ),
        CheckResult(
            name="a_correction_is_a_new_row_and_the_latest_wins",
            passed=correction_ok,
            measured=correction_detail,
            requirement="need 2 rows kept with the later one read back",
        ),
        CheckResult(
            name="export_to_parquet_reads_back_identically",
            passed=export_ok,
            measured=export_detail,
            requirement=f"need {EXPECTED_TABLES} Parquet files with matching row counts",
        ),
    ]
