"""Baseline: enable the extensions this project depends on.

No tables yet. Tables arrive with the steps that need them - journal in 1.7, bars
in 1.3, news in 1.5. This migration exists so a fresh database is provably ready,
and so "did the extensions get enabled?" is answered by migration history rather
than by someone's memory of running a command once.

Revision ID: 0001
Revises:
Created: 2026-10-15
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # TimescaleDB: hypertables for ticks and bars (step 1.3).
    op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")
    # pgvector: embeddings for near-duplicate news detection (step 1.5).
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")


def downgrade() -> None:
    # Deliberately does nothing. Dropping timescaledb would destroy every hypertable
    # in the database, which is not a reasonable response to rolling back one
    # migration. Use `make db-reset` to start clean in development.
    pass
