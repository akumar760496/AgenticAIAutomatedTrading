"""Where news and filings are kept.

Writes are idempotent on the source's own identifier, because a poller re-reads
an overlapping window every run by design: a window that only ever moves forward
loses anything that arrived late. Re-ingesting must be free.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from trading_desk.domain.news import Filing, NewsArticle


class NewsStorePort(Protocol):
    """Durable filings and articles."""

    def save_filings(self, filings: Sequence[Filing]) -> int:
        """Store filings, skipping ones already held. Returns the number inserted."""
        ...

    def save_articles(self, articles: Sequence[NewsArticle]) -> int: ...

    def save_sources(self, sources: Sequence[tuple[str, str, str]]) -> int:
        """Record (name, tier, reason) for each publisher. Returns rows written.

        The reason is stored, not just the tier: FR-3.6 requires every
        credibility score to carry the rule that produced it, and a tier with no
        stated reason is a number nobody can argue with.
        """
        ...

    def count_filings(self, symbol: str | None = None) -> int: ...

    def latest_filing_at(self, symbol: str | None = None) -> datetime | None:
        """The most recent filing time held, or None if there are none."""
        ...
