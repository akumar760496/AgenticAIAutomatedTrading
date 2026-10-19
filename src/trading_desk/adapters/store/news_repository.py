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
from datetime import date, datetime, time
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from trading_desk.domain.dedup import content_hash
from trading_desk.domain.enums import SourceTier
from trading_desk.domain.news import Filing, NewsArticle
from trading_desk.domain.timezones import IST
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


SELECT_ARTICLES = text(
    """
    SELECT article_id, headline, body, url, publisher, source_tier,
           published_at, ingested_at, instrument_symbols
    FROM articles
    ORDER BY published_at, article_id
    LIMIT :limit
    """
)


SELECT_FILINGS = text(
    """
    SELECT filing_id, symbol, subject, body, url, filed_at, ingested_at
    FROM filings
    ORDER BY filed_at, filing_id
    LIMIT :limit
    """
)


SELECT_FILINGS_FOR_SYMBOL = text(
    """
    SELECT filing_id, symbol, subject, body, url, filed_at, ingested_at
    FROM filings
    WHERE symbol = :symbol AND filed_at >= :since
    ORDER BY filed_at, filing_id
    LIMIT :limit
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

    def all_articles(self, limit: int) -> list[NewsArticle]:
        with session_scope(self._factory) as session:
            rows = session.execute(SELECT_ARTICLES, {"limit": limit}).mappings().all()
        return [_to_article(dict(row)) for row in rows]

    def all_filings(self, limit: int) -> list[Filing]:
        with session_scope(self._factory) as session:
            rows = session.execute(SELECT_FILINGS, {"limit": limit}).mappings().all()
        return [_to_filing(dict(row)) for row in rows]

    def filings_for(self, symbol: str, since: date, limit: int) -> list[Filing]:
        """One company's filings from `since` onward, oldest first.

        `since` is a trading date; the column is a timestamp, so it is widened
        to midnight IST rather than midnight UTC. Comparing a date against a UTC
        timestamp would silently drop the first five and a half hours of the day.
        """
        start = datetime.combine(since, time.min, tzinfo=IST)
        with session_scope(self._factory) as session:
            rows = (
                session.execute(
                    SELECT_FILINGS_FOR_SYMBOL,
                    {"symbol": symbol, "since": start, "limit": limit},
                )
                .mappings()
                .all()
            )
        return [_to_filing(dict(row)) for row in rows]

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


def _to_article(row: dict[str, Any]) -> NewsArticle:
    symbols = row.get("instrument_symbols") or []
    return NewsArticle(
        article_id=str(row["article_id"]),
        headline=str(row["headline"]),
        body=str(row["body"]),
        url=str(row["url"]),
        publisher=str(row["publisher"]),
        source_tier=SourceTier(str(row["source_tier"])),
        published_at=row["published_at"],
        ingested_at=row["ingested_at"],
        instrument_symbols=tuple(str(symbol) for symbol in symbols),
    )


def _to_filing(row: dict[str, Any]) -> Filing:
    return Filing(
        filing_id=str(row["filing_id"]),
        symbol=str(row["symbol"]),
        subject=str(row["subject"]),
        body=str(row["body"]),
        url=str(row["url"]),
        filed_at=row["filed_at"],
        ingested_at=row["ingested_at"],
    )
