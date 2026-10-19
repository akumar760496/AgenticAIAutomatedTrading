"""In-memory filings and articles, idempotent like the real store."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from trading_desk.domain.news import Filing, NewsArticle


class FakeNewsStore:
    """Keyed by the source's own id, so a re-ingest inserts nothing."""

    def __init__(self) -> None:
        self.filings: dict[str, Filing] = {}
        self.articles: dict[str, NewsArticle] = {}
        self.sources: dict[str, tuple[str, str]] = {}

    def save_filings(self, filings: Sequence[Filing]) -> int:
        inserted = 0
        for filing in filings:
            if filing.filing_id in self.filings:
                continue
            self.filings[filing.filing_id] = filing
            inserted += 1
        return inserted

    def save_articles(self, articles: Sequence[NewsArticle]) -> int:
        inserted = 0
        for article in articles:
            if article.article_id in self.articles:
                continue
            self.articles[article.article_id] = article
            inserted += 1
        return inserted

    def save_sources(self, sources: Sequence[tuple[str, str, str]]) -> int:
        for name, tier, reason in sources:
            self.sources[name] = (tier, reason)
        return len(sources)

    def count_filings(self, symbol: str | None = None) -> int:
        if symbol is None:
            return len(self.filings)
        return sum(1 for filing in self.filings.values() if filing.symbol == symbol)

    def latest_filing_at(self, symbol: str | None = None) -> datetime | None:
        times = [
            filing.filed_at
            for filing in self.filings.values()
            if symbol is None or filing.symbol == symbol
        ]
        return max(times) if times else None

    def all_articles(self, limit: int) -> list[NewsArticle]:
        """Oldest first, matching the Postgres store's ORDER BY."""
        ordered = sorted(
            self.articles.values(), key=lambda article: (article.published_at, article.article_id)
        )
        return ordered[:limit]

    def all_filings(self, limit: int) -> list[Filing]:
        ordered = sorted(
            self.filings.values(), key=lambda filing: (filing.filed_at, filing.filing_id)
        )
        return ordered[:limit]
