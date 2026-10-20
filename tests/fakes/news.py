"""In-memory news and filings."""

from __future__ import annotations

from datetime import datetime

from trading_desk.domain.news import FeedFailure, NewsArticle


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
