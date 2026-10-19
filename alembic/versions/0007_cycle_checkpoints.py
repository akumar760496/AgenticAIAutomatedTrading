"""Cycle checkpoints: node runs and cached model responses.

What a crashed cycle needs to not start from nothing. Two tables because they
answer two questions - "where did it get to?" and "what did the model already
say?" - and only the second one costs money to re-derive.

Three decisions worth stating:

1. **Append-only, like the journal (0005) and the ledger (0006).** A checkpoint
   that can be edited cannot answer "what did this cycle actually do?", which
   is the only question it is for. UPDATE, DELETE and TRUNCATE all refused;
   TRUNCATE needs its own statement-level trigger because it does not fire
   row-level ones.
2. **`cycle_responses` is unique on (cycle_id, call_key)** and inserts use
   ON CONFLICT DO NOTHING. A replayed call must not look like a second call to
   anything reading this back, and the uniqueness is what makes that true even
   if two writers race.
3. **The cache is scoped to one cycle, by key and by index.** A global response
   cache would be a different and much more dangerous feature: it would let two
   different days' cycles share an answer about two different market states.
   Scoping to the cycle means it can only ever replay interrupted work.

`payload` holds the validated model response as JSON. Model output only - no
prompt and no credential (CLAUDE.md rule 8).

Revision ID: 0007
Revises: 0006
Created: 2026-10-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APPEND_ONLY_GUARD = """
CREATE OR REPLACE FUNCTION cycle_store_is_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION '% is append-only: % is refused', TG_TABLE_NAME, TG_OP;
END;
$$ LANGUAGE plpgsql;
"""

TABLES = ("cycle_nodes", "cycle_responses")


def upgrade() -> None:
    op.create_table(
        "cycle_nodes",
        sa.Column("run_id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("cycle_id", sa.Text, nullable=False),
        sa.Column("node", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("detail", sa.Text, nullable=False, server_default=""),
        # The node's measured values. Enough to rebuild the delegation tree and
        # to read in a trace; deliberately not enough to resume from - see the
        # DEVLOG entry for step 3.4.
        sa.Column("measurements", sa.JSON, nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('STARTED', 'COMPLETED', 'FAILED')", name="ck_cycle_nodes_status"
        ),
    )
    op.create_index("ix_cycle_nodes_cycle", "cycle_nodes", ["cycle_id", "run_id"])

    op.create_table(
        "cycle_responses",
        sa.Column("response_id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("cycle_id", sa.Text, nullable=False),
        sa.Column("node", sa.Text, nullable=False),
        sa.Column("call_key", sa.Text, nullable=False),
        sa.Column("model", sa.Text, nullable=False),
        sa.Column("payload", sa.Text, nullable=False),
        sa.Column("input_tokens", sa.Integer, nullable=False),
        sa.Column("output_tokens", sa.Integer, nullable=False),
        sa.UniqueConstraint("cycle_id", "call_key", name="uq_cycle_responses_cycle_call"),
    )

    op.execute(APPEND_ONLY_GUARD)
    for table in TABLES:
        op.execute(
            f"""
            CREATE TRIGGER trg_{table}_no_update_delete
            BEFORE UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION cycle_store_is_append_only()
            """
        )
        op.execute(
            f"""
            CREATE TRIGGER trg_{table}_no_truncate
            BEFORE TRUNCATE ON {table}
            FOR EACH STATEMENT EXECUTE FUNCTION cycle_store_is_append_only()
            """
        )


def downgrade() -> None:
    for table in TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_no_truncate ON {table}")
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_no_update_delete ON {table}")
    op.execute("DROP FUNCTION IF EXISTS cycle_store_is_append_only()")
    op.drop_table("cycle_responses")
    op.drop_index("ix_cycle_nodes_cycle", table_name="cycle_nodes")
    op.drop_table("cycle_nodes")
