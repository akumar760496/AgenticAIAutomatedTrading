"""The structure as numbers, so an idea can be scored.

Migration 0008 gave an idea its stop, target and expiry. This one gives it the
thing those three cannot substitute for: **the legs, with the premium each was
quoted at**. `docs/06` exits on "60% of spread width" and "50% of the debit
lost", both of which are fractions of a premium, so without an entry premium
nothing in the rulebook can be evaluated and FR-7.6 is unreachable.

**Why a premium is recorded at all.** `ProposedLeg` deliberately has no price:
a proposal is research, and a price on it starts to look like an instruction
(CLAUDE.md rule 1). The premium here is an *observation* - what the chain
printed at briefing time - stored on the journal, which is a record of what
happened rather than something the system hands you. The distinction is the
same one `briefing_ideas.stop_level_paise` already relies on.

**A separate table, not JSON on `briefing_ideas`.** A leg has five fields that
all need checking, and a check constraint cannot reach inside a JSONB blob. A
zero premium or a zero strike is exactly the silent wrong value rule 6 exists
to stop, so each one gets a column and a constraint. It also makes "which
strikes do we keep proposing?" a query rather than a parse.

**Append-only, with the same teeth as the rest of the journal.** The table is
added to `journal_is_append_only()`'s trigger set, row-level for UPDATE and
DELETE and statement-level for TRUNCATE - because TRUNCATE does not fire row
triggers, which 0005 found out by measuring rather than assuming (ISS-019).

`outcomes` also gains four columns: the resolution `basis`, and the gross and
cost figures behind the net P&L. `basis` says whether a stop was seen at a
snapshot, at expiry, or on thesis invalidation. The archive holds three chain
snapshots a day, so a stop is detected at 09:30, 12:30 or 15:15 and not when it
was breached - and a reader cannot tell that from the P&L alone.

Revision ID: 0009
Revises: 0008
Created: 2026-10-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LEGS_TABLE = "briefing_idea_legs"


def upgrade() -> None:
    op.create_table(
        LEGS_TABLE,
        sa.Column("leg_id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "idea_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("briefing_ideas.idea_id"),
            nullable=False,
        ),
        # Ordinal, so the legs come back in the order they were briefed. A
        # vertical read back the other way round would have its net debit
        # negated and be scored as a credit spread.
        sa.Column("leg_index", sa.SmallInteger, nullable=False),
        # `option_right`, not `right`: RIGHT is a SQL reserved word (RIGHT
        # JOIN), so a column called `right` needs quoting at every single use
        # site and one missed quote is a runtime syntax error. Measured, not
        # guessed - the unquoted check constraint failed to parse.
        sa.Column("option_right", sa.Text, nullable=False),
        sa.Column("action", sa.Text, nullable=False),
        sa.Column("strike_paise", sa.BigInteger, nullable=False),
        sa.Column("expiry_date", sa.Date, nullable=False),
        # The mid of bid and ask at briefing time, in paise. The mid rather than
        # the last trade: an illiquid strike's last print can be hours stale and
        # on the wrong side (ISS-007 is that failure in its other form).
        sa.Column("entry_premium_paise", sa.BigInteger, nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("option_right IN ('CALL', 'PUT')", name="ck_idea_legs_right"),
        sa.CheckConstraint("action IN ('BUY', 'SELL')", name="ck_idea_legs_action"),
        sa.CheckConstraint("strike_paise > 0", name="ck_idea_legs_strike_positive"),
        sa.CheckConstraint("entry_premium_paise > 0", name="ck_idea_legs_premium_positive"),
        sa.CheckConstraint("leg_index >= 0", name="ck_idea_legs_index_positive"),
        sa.UniqueConstraint("idea_id", "leg_index", name="uq_idea_legs_idea_index"),
    )
    op.create_index("ix_idea_legs_idea", LEGS_TABLE, ["idea_id", "leg_index"])

    # Nullable, like 0008's levels and for the same reason: every outcome
    # recorded before this migration has no gross or cost figure, and a
    # backfilled zero would claim a costless trade for ever.
    op.add_column("outcomes", sa.Column("gross_pnl_paise", sa.BigInteger, nullable=True))
    op.add_column("outcomes", sa.Column("costs_paise", sa.BigInteger, nullable=True))
    # Defaulted rather than nullable: SNAPSHOT is the honest description of how
    # every resolution is measured today, so there is no row for which it is a
    # guess. Later rows state their own basis.
    op.add_column(
        "outcomes",
        sa.Column("basis", sa.Text, nullable=False, server_default="SNAPSHOT"),
    )
    op.add_column("outcomes", sa.Column("measured", sa.Text, nullable=False, server_default=""))
    op.add_column("outcomes", sa.Column("threshold", sa.Text, nullable=False, server_default=""))
    op.create_check_constraint(
        "ck_outcomes_basis",
        "outcomes",
        "basis IN ('SNAPSHOT', 'EXPIRY', 'INVALIDATION')",
    )
    op.create_check_constraint(
        "ck_outcomes_costs_not_negative",
        "outcomes",
        "costs_paise IS NULL OR costs_paise >= 0",
    )

    # The legs table joins the append-only set. The function already exists from
    # 0005; only the triggers are per-table.
    op.execute(
        f"""
        CREATE TRIGGER trg_{LEGS_TABLE}_append_only
        BEFORE UPDATE OR DELETE ON {LEGS_TABLE}
        FOR EACH ROW EXECUTE FUNCTION journal_is_append_only();
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER trg_{LEGS_TABLE}_no_truncate
        BEFORE TRUNCATE ON {LEGS_TABLE}
        FOR EACH STATEMENT EXECUTE FUNCTION journal_is_append_only();
        """
    )


def downgrade() -> None:
    op.execute(f"DROP TRIGGER IF EXISTS trg_{LEGS_TABLE}_append_only ON {LEGS_TABLE}")
    op.execute(f"DROP TRIGGER IF EXISTS trg_{LEGS_TABLE}_no_truncate ON {LEGS_TABLE}")
    op.drop_table(LEGS_TABLE)
    op.drop_constraint("ck_outcomes_costs_not_negative", "outcomes", type_="check")
    op.drop_constraint("ck_outcomes_basis", "outcomes", type_="check")
    for column in ("threshold", "measured", "basis", "costs_paise", "gross_pnl_paise"):
        op.drop_column("outcomes", column)
