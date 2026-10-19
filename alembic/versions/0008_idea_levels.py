"""The three numbers an idea has to be reviewed against.

`briefing_ideas` already stored a stop and a target - as English, inside the
`gate_values` JSON: "₹1,400 - below the long call strike the spread is working
against you". That is the right thing to put in a briefing and the wrong thing
to compare a closing price to, and the 15:45 review has to compare a closing
price to something.

So three nullable columns, carrying numbers that were on `TradeProposal` all
along: `stop_level_paise`, `target_level_paise`, `expiry_date`. Paise as
BigInteger, like every other money column in this schema - rule 4.

**Nullable on purpose, and they stay nullable.** Every idea briefed before this
migration genuinely has no recorded level, and the honest answer for those is
"cannot tell", which `domain/review.py` returns. A NOT NULL column with a
backfilled zero would make them look reviewed, at a stop of ₹0, for ever. Ideas
written from here on always carry all three.

The table is append-only (0005) and remains so: that guard is enforced by row
and statement triggers on UPDATE, DELETE and TRUNCATE, none of which this
touches.

Revision ID: 0008
Revises: 0007
Created: 2026-10-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("briefing_ideas", sa.Column("stop_level_paise", sa.BigInteger, nullable=True))
    op.add_column("briefing_ideas", sa.Column("target_level_paise", sa.BigInteger, nullable=True))
    op.add_column("briefing_ideas", sa.Column("expiry_date", sa.Date, nullable=True))

    # A level of zero is not a level. The columns are optional, so the
    # constraint has to allow NULL and refuse a number that cannot be real.
    op.create_check_constraint(
        "ck_briefing_ideas_stop_positive",
        "briefing_ideas",
        "stop_level_paise IS NULL OR stop_level_paise > 0",
    )
    op.create_check_constraint(
        "ck_briefing_ideas_target_positive",
        "briefing_ideas",
        "target_level_paise IS NULL OR target_level_paise > 0",
    )

    # The review reads open ideas by date on every run.
    op.create_index("ix_briefing_ideas_expiry", "briefing_ideas", ["expiry_date"])


def downgrade() -> None:
    op.drop_index("ix_briefing_ideas_expiry", table_name="briefing_ideas")
    op.drop_constraint("ck_briefing_ideas_target_positive", "briefing_ideas", type_="check")
    op.drop_constraint("ck_briefing_ideas_stop_positive", "briefing_ideas", type_="check")
    op.drop_column("briefing_ideas", "expiry_date")
    op.drop_column("briefing_ideas", "target_level_paise")
    op.drop_column("briefing_ideas", "stop_level_paise")
