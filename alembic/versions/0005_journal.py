"""The journal: the project's evidence base.

Built in Phase 1 with nothing yet to record, because **it cannot be
backfilled**. The question "was the system's confidence honest?" can only be
answered from rows written at the time the call was made.

Five decisions here that are hard to reverse once there is data:

1. **Append-only is enforced by the database, not by the adapter.** Triggers
   reject UPDATE, DELETE *and TRUNCATE* on every table below. An adapter that
   promises not to overwrite is a promise; a trigger is evidence. A journal that
   can be rewritten proves nothing about what was believed at the time.
   TRUNCATE needs its own statement-level trigger - it does not fire row-level
   ones, and it emptied the table cleanly when only the row trigger existed.
2. **A correction is a new row.** `user_decisions` and `outcomes` therefore have
   a surrogate primary key and *no* unique constraint on idea_id - changing your
   mind is expected, and the history of changing it is itself data. Reads take
   the latest row by `recorded_at`.
3. **`decided_at` and `recorded_at` are separate.** When you made the decision
   is not when it reached the database. A decision entered three days late is a
   different quality of evidence, and only the gap shows it.
4. **`reason` is NOT NULL with a non-empty CHECK.** docs/06 calls it the most
   valuable field in the schema. A nullable column collects nulls.
5. **Every gate's value is stored, not just the verdict** (`gate_values` JSONB).
   Storing PASS/FAIL discards the number, and the number is what lets you ask
   later which gate was actually predictive. CLAUDE.md rule 9.

Money is BIGINT paise throughout, never NUMERIC or DOUBLE (rule 4).

Revision ID: 0005
Revises: 0004
Created: 2026-10-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APPEND_ONLY_TABLES = (
    "briefing_ideas",
    "user_decisions",
    "outcomes",
    "override_log",
    "calibration_snapshots",
)


def upgrade() -> None:
    op.create_table(
        "briefing_ideas",
        sa.Column("idea_id", postgresql.UUID(as_uuid=True), primary_key=True),
        # One briefing run. Lets you ask "what else was on the page that day?",
        # which is how you tell a good call from a lucky one out of ten.
        sa.Column("cycle_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("symbol", sa.Text, nullable=False),
        sa.Column("setup_id", sa.Text, nullable=False),
        sa.Column("direction", sa.Text, nullable=False),
        # Free text, not an enum: v1 briefs a structure in prose and pinning an
        # enum now would force a migration the first time a new one appears.
        sa.Column("structure", sa.Text, nullable=False, server_default=""),
        sa.Column("rank", sa.SmallInteger, nullable=False),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=False),
        # Every gate's measured value, not its verdict. See note 5.
        sa.Column("gate_values", postgresql.JSONB, nullable=False),
        sa.Column("hypothetical_risk_paise", sa.BigInteger, nullable=False),
        sa.Column("invalidation", sa.Text, nullable=False, server_default=""),
        sa.Column("agent_rationale", sa.Text, nullable=False, server_default=""),
        # True when iv_rank came from the realised-vol proxy rather than real IV
        # history (step 1.8). Without it, six months of proxy-based ideas are
        # indistinguishable from real ones in the calibration maths.
        sa.Column("used_iv_proxy", sa.Boolean, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name="ck_briefing_ideas_confidence_range"
        ),
        sa.CheckConstraint("rank >= 1", name="ck_briefing_ideas_rank_positive"),
    )
    op.create_index("ix_briefing_ideas_cycle", "briefing_ideas", ["cycle_id"])
    op.create_index("ix_briefing_ideas_created", "briefing_ideas", ["created_at"])

    op.create_table(
        "user_decisions",
        # Surrogate key, and deliberately no unique constraint on idea_id: a
        # correction is a new row. See note 2.
        sa.Column("decision_id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "idea_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("briefing_ideas.idea_id"),
            nullable=False,
        ),
        sa.Column("action", sa.Text, nullable=False),
        sa.Column("reason", sa.Text, nullable=False),
        # Null unless the idea was taken. Size is the user's own number: an LLM
        # never sizes a position (CLAUDE.md rule 1), it is recorded after the
        # fact for outcome maths.
        sa.Column("entry_fill_paise", sa.BigInteger, nullable=True),
        sa.Column("size", sa.Integer, nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "action IN ('TOOK', 'SKIPPED', 'MODIFIED')", name="ck_user_decisions_action"
        ),
        # The most valuable field in the schema is not allowed to be blank.
        sa.CheckConstraint("length(btrim(reason)) > 0", name="ck_user_decisions_reason_present"),
    )
    op.create_index("ix_user_decisions_idea", "user_decisions", ["idea_id", "recorded_at"])

    op.create_table(
        "outcomes",
        sa.Column("outcome_id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "idea_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("briefing_ideas.idea_id"),
            nullable=False,
        ),
        sa.Column("resolution", sa.Text, nullable=False),
        # Recorded for skipped ideas too, after full costs. Scoring only the
        # taken ones measures the user's taste, not the system's research.
        sa.Column("hypothetical_pnl_paise", sa.BigInteger, nullable=False),
        sa.Column("actual_pnl_paise", sa.BigInteger, nullable=True),
        # How far it went against you before it worked. A winner that first
        # drew down 3x the stop is not a repeatable idea.
        sa.Column("max_adverse_excursion_paise", sa.BigInteger, nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "resolution IN ('TARGET', 'STOP', 'TIME_STOP', 'INVALIDATED')",
            name="ck_outcomes_resolution",
        ),
    )
    op.create_index("ix_outcomes_idea", "outcomes", ["idea_id", "recorded_at"])

    op.create_table(
        "override_log",
        sa.Column("override_id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "idea_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("briefing_ideas.idea_id"),
            nullable=False,
        ),
        sa.Column("system_position", sa.Text, nullable=False),
        sa.Column("user_action", sa.Text, nullable=False),
        sa.Column("reason", sa.Text, nullable=False),
        # Filled in later, when it is known. Nullable because the disagreement
        # is worth recording before the result exists - waiting for the result
        # is how override logs end up empty.
        sa.Column("what_followed", sa.Text, nullable=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("length(btrim(reason)) > 0", name="ck_override_log_reason_present"),
    )
    op.create_index("ix_override_log_idea", "override_log", ["idea_id"])

    op.create_table(
        "calibration_snapshots",
        sa.Column("snapshot_id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        # Which slice this scores: an agent name, a setup id, or ALL.
        sa.Column("scope", sa.Text, nullable=False),
        sa.Column("brier_score", sa.Numeric(6, 5), nullable=False),
        # The number that makes the Brier score mean anything: always predicting
        # the base rate. A model that cannot beat it is not adding information.
        sa.Column("baseline_brier_score", sa.Numeric(6, 5), nullable=False),
        sa.Column("reliability_bins", postgresql.JSONB, nullable=False),
        sa.Column("sample_count", sa.Integer, nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("sample_count >= 0", name="ck_calibration_sample_count"),
    )
    op.create_index("ix_calibration_scope", "calibration_snapshots", ["scope", "as_of"])

    # The teeth. Without this, "append-only" is a comment in an adapter.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION journal_is_append_only()
        RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION
                'journal table % is append-only: record a correction as a new row',
                TG_TABLE_NAME;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    for table in APPEND_ONLY_TABLES:
        op.execute(
            f"""
            CREATE TRIGGER trg_{table}_append_only
            BEFORE UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION journal_is_append_only();
            """
        )
        # TRUNCATE does not fire row-level triggers, so the row trigger above
        # leaves the journal wipeable in one statement. Measured, not assumed:
        # a TRUNCATE emptied the table cleanly before this was added. It needs
        # its own statement-level trigger.
        op.execute(
            f"""
            CREATE TRIGGER trg_{table}_no_truncate
            BEFORE TRUNCATE ON {table}
            FOR EACH STATEMENT EXECUTE FUNCTION journal_is_append_only();
            """
        )


def downgrade() -> None:
    for table in APPEND_ONLY_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_append_only ON {table}")
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_no_truncate ON {table}")
    op.execute("DROP FUNCTION IF EXISTS journal_is_append_only()")
    op.drop_table("calibration_snapshots")
    op.drop_table("override_log")
    op.drop_table("outcomes")
    op.drop_table("user_decisions")
    op.drop_table("briefing_ideas")
