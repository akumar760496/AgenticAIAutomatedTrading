"""Calendars: cached exchange expiries.

Only expiries get a table. Holidays and macro events live in config with an
effective-from date and stated provenance (CLAUDE.md rule 7) because they are
published facts that change by circular, and a config file is reviewable in a
diff. Expiries are different: they come from the venue, change as contracts are
listed, and are fetched rather than written - so they are cached data, not
configuration.

`fetched_at` is not decoration. An expiry list read three weeks ago may be
missing contracts listed since, and a cache with no age is a cache nobody can
decide whether to trust.

Revision ID: 0003
Revises: 0002
Created: 2026-10-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "expiries",
        sa.Column("instrument_symbol", sa.Text, nullable=False),
        sa.Column("expiry_date", sa.Date, nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        # Whether this expiry sits on the usual weekday. A displaced expiry is
        # evidence of a holiday (ISS-004), so it is worth storing rather than
        # recomputing from a weekday rule later - which is the exact mistake the
        # whole design avoids.
        sa.Column("is_displaced", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.PrimaryKeyConstraint("instrument_symbol", "expiry_date", name="pk_expiries"),
    )
    op.create_index("ix_expiries_date", "expiries", ["expiry_date"])


def downgrade() -> None:
    op.drop_table("expiries")
