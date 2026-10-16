"""In-memory news and filings."""

from __future__ import annotations

from datetime import datetime

from trading_desk.domain.news import Filing, NewsArticle


class FakeNews:
    def __init__(self) -> None:
        self._articles: list[NewsArticle] = []

    def add(self, *articles: NewsArticle) -> None:
        self._articles.extend(articles)

    def fetch_since(self, since: datetime, symbols: list[str]) -> list[NewsArticle]:
        wanted = set(symbols)
        return [
            article
            for article in self._articles
            if article.published_at > since
            and (not wanted or wanted & set(article.instrument_symbols))
        ]


class FakeFilings:
    def __init__(self) -> None:
        self._filings: list[Filing] = []

    def add(self, *filings: Filing) -> None:
        self._filings.extend(filings)

    def fetch_since(self, since: datetime, symbols: list[str]) -> list[Filing]:
        wanted = set(symbols)
        return [
            filing
            for filing in self._filings
            if filing.filed_at > since and (not wanted or filing.symbol in wanted)
        ]
