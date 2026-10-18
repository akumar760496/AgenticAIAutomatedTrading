"""In-memory news and filings."""

from __future__ import annotations

from datetime import datetime

from trading_desk.domain.news import FeedFailure, Filing, NewsArticle


class FakeNews:
    """fail_feeds names sources the next poll reports as unreadable."""

    def __init__(self) -> None:
        self._articles: list[NewsArticle] = []
        self.fail_feeds: list[FeedFailure] = []

    def add(self, *articles: NewsArticle) -> None:
        self._articles.extend(articles)

    def recent_failures(self) -> list[FeedFailure]:
        return list(self.fail_feeds)

    def fetch_since(self, since: datetime, symbols: list[str]) -> list[NewsArticle]:
        wanted = set(symbols)
        return [
            article
            for article in self._articles
            if article.published_at > since
            and (not wanted or wanted & set(article.instrument_symbols))
        ]


class FakeFilings:
    """fail_next_fetch exercises a feed outage: the ingest must report it, not crash."""

    def __init__(self) -> None:
        self._filings: list[Filing] = []
        self.fail_next_fetch = False
        self.calls = 0

    def add(self, *filings: Filing) -> None:
        self._filings.extend(filings)

    def fetch_since(self, since: datetime, symbols: list[str]) -> list[Filing]:
        self.calls += 1
        if self.fail_next_fetch:
            self.fail_next_fetch = False
            raise ConnectionError("Simulated filings feed failure")

        wanted = set(symbols)
        return sorted(
            (
                filing
                for filing in self._filings
                if filing.filed_at > since and (not wanted or filing.symbol in wanted)
            ),
            key=lambda filing: filing.filed_at,
        )
