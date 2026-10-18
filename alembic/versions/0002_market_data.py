"""Market data: ticks, bars and gaps as Timescale hypertables.

Three decisions worth stating, because each is hard to change once there is data:

1. **Prices are BIGINT paise, never NUMERIC or DOUBLE.** CLAUDE.md rule 4. A float
   column would quietly round every price the moment it is written, and no later
   code could tell.
2. **Every table is a hypertable partitioned on time.** A year of 1-minute bars for
   220 instruments is ~20M rows; a plain table makes "last 20 days for one symbol"
   a sequential scan.
3. **`data_gaps` is a first-class table, not a log line.** A gap is the reason a
   backtest result is wrong, and it has to be queryable alongside the bars it
   interrupts. Nothing interpolates, so the gap is the only record that the
   silence was a fault rather than a quiet market.

Revision ID: 0002
Revises: 0001
Created: 2026-10-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BAR_TABLES = ["bars_1m", "bars_5m", "bars_1d"]


def _bar_table(name: str) -> None:
    op.create_table(
        name,
        sa.Column("instrument_symbol", sa.Text, nullable=False),
        sa.Column("security_id", sa.Text, nullable=False),
        sa.Column("bar_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("open_paise", sa.BigInteger, nullable=False),
        sa.Column("high_paise", sa.BigInteger, nullable=False),
        sa.Column("low_paise", sa.BigInteger, nullable=False),
        sa.Column("close_paise", sa.BigInteger, nullable=False),
        sa.Column("volume", sa.BigInteger, nullable=False),
        # The source is kept per row: a bar from the live feed and a bar from the
        # backfill API can disagree, and without this nobody can tell which is which.
        sa.Column("source", sa.Text, nullable=False, server_default="live"),
        sa.PrimaryKeyConstraint("instrument_symbol", "bar_start", name=f"pk_{name}"),
        sa.CheckConstraint("high_paise >= low_paise", name=f"ck_{name}_high_ge_low"),
        sa.CheckConstraint("volume >= 0", name=f"ck_{name}_volume_non_negative"),
    )
    # chunk_time_interval: a week for intraday bars keeps chunks a sensible size.
    op.execute(
        f"SELECT create_hypertable('{name}', 'bar_start', "
        f"chunk_time_interval => INTERVAL '7 days')"
    )


def upgrade() -> None:
    op.create_table(
        "ticks",
        sa.Column("instrument_symbol", sa.Text, nullable=False),
        sa.Column("security_id", sa.Text, nullable=False),
        sa.Column("exchange_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("price_paise", sa.BigInteger, nullable=False),
        # Quantity traded in this tick, not the running day total. See domain.market.Tick.
        sa.Column("quantity", sa.BigInteger, nullable=False),
        sa.Column("sequence", sa.BigInteger, nullable=True),
        sa.CheckConstraint("quantity >= 0", name="ck_ticks_quantity_non_negative"),
    )
    # No primary key: two genuine trades can share a symbol and a timestamp, and a
    # key over those columns would reject real data. Deduplication is the feed's
    # sequence number where it exists - see domain.market.Tick.
    op.execute(
        "SELECT create_hypertable('ticks', 'exchange_time', "
        "chunk_time_interval => INTERVAL '1 day')"
    )
    op.create_index("ix_ticks_symbol_time", "ticks", ["instrument_symbol", "exchange_time"])

    for table in BAR_TABLES:
        _bar_table(table)

    op.create_table(
        "data_gaps",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("instrument_symbol", sa.Text, nullable=True),  # null = the whole feed
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),  # null = still open
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "ended_at IS NULL OR ended_at >= started_at", name="ck_data_gaps_ordered"
        ),
    )
    op.create_index("ix_data_gaps_started_at", "data_gaps", ["started_at"])


def downgrade() -> None:
    op.drop_table("data_gaps")
    for table in reversed(BAR_TABLES):
        op.drop_table(table)
    op.drop_table("ticks")
