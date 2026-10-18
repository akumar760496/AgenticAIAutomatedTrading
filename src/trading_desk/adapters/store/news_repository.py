"""Filings and articles in Postgres.

Idempotent on the source's primary key via ON CONFLICT DO NOTHING, like the bar
store, and for the same reason: the poller deliberately re-reads an overlapping
window, so a second write of the same filing must cost nothing and change
nothing.

DO NOTHING rather than DO UPDATE: an exchange filing is a published fact and does
not change. If the same id ever arrives with a different body, that is a finding
worth investigating, not a row to silently overwrite.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from trading_desk.domain.dedup import content_hash
from trading_desk.domain.news import Filing, NewsArticle
from trading_desk.domain.untrusted import inspect
from trading_desk.infra.db import session_scope

INSERT_FILING = text(
    """
    INSERT INTO filings (
        filing_id, symbol, subject, body, url, filed_at, ingested_at,
        attachments, content_hash, suspicions
    ) VALUES (
        :filing_id, :symbol, :subject, :body, :url, :filed_at, :ingested_at,
        :attachments, :content_hash, :suspicions
    )
    ON CONFLICT (filing_id) DO NOTHING
    """
)

INSERT_ARTICLE = text(
    """
    INSERT INTO articles (
        article_id, headline, body, url, publisher, source_tier,
        published_at, ingested_at, instrument_symbols, content_hash,
        event_key, suspicions
    ) VALUES (
        :article_id, :headline, :body, :url, :publisher, :source_tier,
        :published_at, :ingested_at, :instrument_symbols, :content_hash,
        :event_key, :suspicions
    )
    ON CONFLICT (article_id) DO NOTHING
    """
)


UPSERT_SOURCE = text(
    """
    INSERT INTO sources (name, tier, tier_reason)
    VALUES (:name, :tier, :tier_reason)
    ON CONFLICT (name) DO UPDATE
        SET tier = EXCLUDED.tier, tier_reason = EXCLUDED.tier_reason
    """
)


class PostgresNewsStore:
    """Implements NewsStorePort against the filings and articles tables."""

    def __init__(self, factory: sessionmaker[Session]) -> None:
        self._factory = factory

    def save_filings(self, filings: Sequence[Filing]) -> int:
        if not filings:
            return 0

        rows = [
            {
                "filing_id": filing.filing_id,
                "symbol": filing.symbol,
                "subject": filing.subject,
                "body": filing.body,
                "url": filing.url,
                "filed_at": filing.filed_at,
                "ingested_at": filing.ingested_at,
                "attachments": list(filing.attachments),
                "content_hash": content_hash(filing.subject, filing.body),
                "suspicions": list(inspect(filing.body).suspicions),
            }
            for filing in filings
        ]

        with session_scope(self._factory) as session:
            before = _count(session, "filings", None)
            session.execute(INSERT_FILING, rows)
            session.flush()
            return _count(session, "filings", None) - before

    def save_articles(self, articles: Sequence[NewsArticle]) -> int:
        if not articles:
            return 0

        rows = [
            {
                "article_id": article.article_id,
                "headline": article.headline,
                "body": article.body,
                "url": article.url,
                "publisher": article.publisher,
                "source_tier": str(article.source_tier),
                "published_at": article.published_at,
                "ingested_at": article.ingested_at,
                "instrument_symbols": list(article.instrument_symbols),
                "content_hash": content_hash(article.headline, article.body),
                "event_key": article.event_key,
                "suspicions": list(inspect(article.body).suspicions),
            }
            for article in articles
        ]

        with session_scope(self._factory) as session:
            before = _count(session, "articles", None)
            session.execute(INSERT_ARTICLE, rows)
            session.flush()
            return _count(session, "articles", None) - before

    def save_sources(self, sources: Sequence[tuple[str, str, str]]) -> int:
        if not sources:
            return 0
        rows = [
            {"name": name, "tier": tier, "tier_reason": reason} for name, tier, reason in sources
        ]
        with session_scope(self._factory) as session:
            session.execute(UPSERT_SOURCE, rows)
        return len(rows)

    def count_filings(self, symbol: str | None = None) -> int:
        with session_scope(self._factory) as session:
            return _count(session, "filings", symbol)

    def latest_filing_at(self, symbol: str | None = None) -> datetime | None:
        clause = "WHERE symbol = :symbol" if symbol else ""
        with session_scope(self._factory) as session:
            return session.execute(
                text(f"SELECT max(filed_at) FROM filings {clause}"),
                {"symbol": symbol} if symbol else {},
            ).scalar_one_or_none()


def _count(session: Session, table: str, symbol: str | None) -> int:
    clause = "WHERE symbol = :symbol" if symbol else ""
    result = session.execute(
        text(f"SELECT count(*) FROM {table} {clause}"),
        {"symbol": symbol} if symbol else {},
    ).scalar_one()
    return int(result)
