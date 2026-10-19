"""The LLM spend ledger.

One row per paid model call. It exists because the monthly cap has to survive a
restart: a counter held in a process resets every time the scheduler is
bounced, and a budget that resets is not a budget.

Three decisions worth stating:

1. **Append-only, enforced by a trigger**, exactly like the journal (0005).
   A spend ledger that can be edited cannot answer "did we stay under the cap
   in March?", which is the only question it is for. UPDATE, DELETE and
   TRUNCATE are all refused - TRUNCATE needs its own statement-level trigger
   because it does not fire row-level ones.
2. **Cost is BIGINT paise** (rule 4), alongside the raw token counts. Storing
   only the cost would make a price change un-recomputable; storing only the
   tokens would make every read do arithmetic. Both are cheap.
3. **`called_at` is timestamptz** and the monthly total is grouped in IST,
   because the cap is a figure Ajay set in rupees per calendar month as he
   experiences it. Anthropic bills in UTC, so the two disagree about calls made
   in the last 5.5 hours of a month. That gap is reconciled against the console,
   not hidden by pretending the boundary is the same.

Revision ID: 0006
Revises: 0005
Created: 2026-10-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APPEND_ONLY_GUARD = """
CREATE OR REPLACE FUNCTION llm_calls_is_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'llm_calls is append-only: % is refused', TG_OP;
END;
$$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    op.create_table(
        "llm_calls",
        sa.Column("call_id", sa.BigInteger, primary_key=True, autoincrement=True),
        # Which research cycle paid for it. The per-node cost report groups on
        # these two, and a briefing that cannot say what it cost is not auditable.
        sa.Column("cycle_id", sa.Text, nullable=False),
        sa.Column("node", sa.Text, nullable=False),
        sa.Column("tier", sa.Text, nullable=False),
        sa.Column("model", sa.Text, nullable=False),
        sa.Column("input_tokens", sa.Integer, nullable=False),
        sa.Column("output_tokens", sa.Integer, nullable=False),
        sa.Column("cost_paise", sa.BigInteger, nullable=False),
        sa.Column("called_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("input_tokens >= 0", name="ck_llm_calls_input_tokens_non_negative"),
        sa.CheckConstraint("output_tokens >= 0", name="ck_llm_calls_output_tokens_non_negative"),
        sa.CheckConstraint("cost_paise >= 0", name="ck_llm_calls_cost_non_negative"),
    )
    # The cap reads this on every single call, so it is the one index that has
    # to be there from the start.
    op.create_index("ix_llm_calls_called_at", "llm_calls", ["called_at"])
    op.create_index("ix_llm_calls_cycle", "llm_calls", ["cycle_id"])

    op.execute(APPEND_ONLY_GUARD)
    op.execute(
        """
        CREATE TRIGGER trg_llm_calls_no_update_delete
        BEFORE UPDATE OR DELETE ON llm_calls
        FOR EACH ROW EXECUTE FUNCTION llm_calls_is_append_only()
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_llm_calls_no_truncate
        BEFORE TRUNCATE ON llm_calls
        FOR EACH STATEMENT EXECUTE FUNCTION llm_calls_is_append_only()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_llm_calls_no_truncate ON llm_calls")
    op.execute("DROP TRIGGER IF EXISTS trg_llm_calls_no_update_delete ON llm_calls")
    op.execute("DROP FUNCTION IF EXISTS llm_calls_is_append_only()")
    op.drop_index("ix_llm_calls_cycle", table_name="llm_calls")
    op.drop_index("ix_llm_calls_called_at", table_name="llm_calls")
    op.drop_table("llm_calls")
