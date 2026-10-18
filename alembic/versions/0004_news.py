"""News and filings: raw bodies, kept verbatim.

Design notes that are hard to reverse once there is data:

1. **The raw body is stored, never a summary.** A summary discards the wording
   the credibility scorer reads, and you cannot un-summarise.
2. **published_at and ingested_at are separate columns.** The gap between them is
   how late we were, and it is the only way to tell "the market was quiet" from
   "our poller was down" three months later.
3. **`embedding` is `vector` with no dimension.** Near-duplicate detection needs
   a model that has not been chosen, and pinning 384 or 1024 now would mean a
   migration later. An ANN index does need a fixed dimension - that index is
   added with the model, not before it.
4. **`event_key` groups sources.** Five outlets carrying one wire story share a
   key, so a credibility gate counts one event with five sources rather than
   five pieces of evidence.
5. **`suspicions` records a possible prompt injection** (FR-3.9) without
   filtering the row. A story somebody tried to poison is itself information.

Revision ID: 0004
Revises: 0003
Created: 2026-10-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sources",
        sa.Column("name", sa.Text, primary_key=True),
        sa.Column("tier", sa.Text, nullable=False),
        # Why this tier, in words. FR-3.6: every score carries its rule.
        sa.Column("tier_reason", sa.Text, nullable=False, server_default=""),
        sa.Column("licence_note", sa.Text, nullable=False, server_default=""),
    )

    op.create_table(
        "filings",
        sa.Column("filing_id", sa.Text, primary_key=True),
        sa.Column("symbol", sa.Text, nullable=False),
        sa.Column("subject", sa.Text, nullable=False),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("url", sa.Text, nullable=False),
        sa.Column("filed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attachments", sa.ARRAY(sa.Text), nullable=False, server_default="{}"),
        sa.Column("content_hash", sa.Text, nullable=False),
        sa.Column("suspicions", sa.ARRAY(sa.Text), nullable=False, server_default="{}"),
    )
    op.create_index("ix_filings_symbol_filed_at", "filings", ["symbol", "filed_at"])
    op.create_index("ix_filings_content_hash", "filings", ["content_hash"])

    op.create_table(
        "articles",
        sa.Column("article_id", sa.Text, primary_key=True),
        sa.Column("headline", sa.Text, nullable=False),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("url", sa.Text, nullable=False),
        sa.Column("publisher", sa.Text, nullable=False),
        sa.Column("source_tier", sa.Text, nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("instrument_symbols", sa.ARRAY(sa.Text), nullable=False, server_default="{}"),
        sa.Column("content_hash", sa.Text, nullable=False),
        sa.Column("event_key", sa.Text, nullable=True),
        sa.Column("suspicions", sa.ARRAY(sa.Text), nullable=False, server_default="{}"),
    )
    op.execute("ALTER TABLE articles ADD COLUMN embedding vector")
    op.create_index("ix_articles_published_at", "articles", ["published_at"])
    op.create_index("ix_articles_event_key", "articles", ["event_key"])
    op.create_index("ix_articles_content_hash", "articles", ["content_hash"])


def downgrade() -> None:
    op.drop_table("articles")
    op.drop_table("filings")
    op.drop_table("sources")
