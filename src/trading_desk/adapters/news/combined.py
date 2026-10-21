"""Several news sources behind one NewsPort.

`ArticleIngest` takes a single port, and it should stay that way: grouping and
syndication counting are the whole point of the ingest, and they only work if
every source's articles arrive in the same pass. Five outlets carrying one story
count as one event with five sources - which cannot happen if each source is
ingested separately.

So the fan-out lives here rather than in the application layer, and the ingest
goes on seeing one port.

**A source that fails must not take the others down with it.** SEBI's RSS and
Drishti fail for unrelated reasons - a TLS root on one side, a rate limit on the
other - and losing the regulator's orders because a vendor returned 429 would be
the worst of both. Each source is read independently and its failures are passed
through with the source's name attached, so the ingest can still tell a dead
feed from a quiet day (CLAUDE.md rule 6).
"""

from __future__ import annotations

from datetime import datetime

from trading_desk.domain.news import FeedFailure, NewsArticle
from trading_desk.ports.news import NewsPort


class CombinedNewsAdapter:
    """Implements NewsPort by reading every source given to it."""

    def __init__(self, sources: dict[str, NewsPort]) -> None:
        if not sources:
            raise ValueError("CombinedNewsAdapter needs at least one source")
        self._sources = sources

    def fetch_since(self, since: datetime, symbols: list[str]) -> list[NewsArticle]:
        """Every source's articles, oldest first, each article kept once.

        Keyed by article_id, which each adapter namespaces with its own prefix
        (`drishti:`, `filing:`), so two sources cannot collide by accident.
        """
        by_id: dict[str, NewsArticle] = {}
        for port in self._sources.values():
            for article in port.fetch_since(since, symbols):
                by_id.setdefault(article.article_id, article)
        return sorted(by_id.values(), key=lambda article: article.published_at)

    def recent_failures(self) -> list[FeedFailure]:
        """Failures from the last `fetch_since`, prefixed with the source name.

        Without the prefix, "HTTP 429" in an alert does not say which source
        stopped answering, and the two have completely different remedies.
        """
        failures = []
        for name, port in self._sources.items():
            for failure in port.recent_failures():
                failures.append(FeedFailure(f"{name}: {failure.name}", failure.reason))
        return failures

    def _failed_sources(self) -> set[str]:
        return {name for name, port in self._sources.items() if port.recent_failures()}

    def every_source_failed(self) -> bool:
        """True when nothing could be read at all.

        The distinction the ingest needs: one source down is degraded output
        worth alerting on, every source down means the briefing has no news
        basis and must not claim one.
        """
        return self._failed_sources() == set(self._sources)
