"""Several sources behind one NewsPort.

The guard that matters here was induced by the thing it prevents: one source
failing must not take the others' articles with it. SEBI's RSS and a paid vendor
fail for unrelated reasons, and losing the regulator because a vendor rate-limited
us would be the worst of both.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from trading_desk.adapters.news.combined import CombinedNewsAdapter
from trading_desk.domain.enums import SourceTier
from trading_desk.domain.news import FeedFailure, NewsArticle

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
SINCE = NOW - timedelta(days=7)


def _article(article_id: str, minutes: int = 0) -> NewsArticle:
    return NewsArticle(
        article_id=article_id,
        headline=f"headline {article_id}",
        body="body",
        url="https://example.test",
        publisher="Someone",
        source_tier=SourceTier.TIER_2,
        published_at=NOW - timedelta(minutes=minutes),
        ingested_at=NOW,
    )


class StubSource:
    """A NewsPort that returns what the test tells it to."""

    def __init__(
        self,
        articles: list[NewsArticle] | None = None,
        failures: list[FeedFailure] | None = None,
    ) -> None:
        self._articles = articles or []
        self._failures = failures or []
        self.calls: list[tuple[datetime, list[str]]] = []

    def fetch_since(self, since: datetime, symbols: list[str]) -> list[NewsArticle]:
        self.calls.append((since, symbols))
        return list(self._articles)

    def recent_failures(self) -> list[FeedFailure]:
        return list(self._failures)


class TestEverySourceIsRead:
    def test_articles_from_all_sources_are_merged(self) -> None:
        combined = CombinedNewsAdapter(
            {
                "SEBI": StubSource([_article("filing:1")]),
                "Drishti": StubSource([_article("drishti:2")]),
            }
        )
        ids = [a.article_id for a in combined.fetch_since(SINCE, ["RELIANCE"])]
        assert sorted(ids) == ["drishti:2", "filing:1"]

    def test_each_source_gets_the_same_window_and_symbols(self) -> None:
        sebi, drishti = StubSource(), StubSource()
        combined = CombinedNewsAdapter({"SEBI": sebi, "Drishti": drishti})
        combined.fetch_since(SINCE, ["RELIANCE", "TCS"])

        assert sebi.calls == [(SINCE, ["RELIANCE", "TCS"])]
        assert drishti.calls == sebi.calls

    def test_articles_come_back_oldest_first(self) -> None:
        combined = CombinedNewsAdapter(
            {
                "a": StubSource([_article("new", minutes=1)]),
                "b": StubSource([_article("old", minutes=90)]),
            }
        )
        ids = [a.article_id for a in combined.fetch_since(SINCE, ["X"])]
        assert ids == ["old", "new"]

    def test_the_same_id_from_two_sources_is_stored_once(self) -> None:
        shared = _article("drishti:same")
        combined = CombinedNewsAdapter({"a": StubSource([shared]), "b": StubSource([shared])})
        assert len(combined.fetch_since(SINCE, ["X"])) == 1

    def test_no_sources_is_refused_at_construction(self) -> None:
        """An empty set of sources returns zero articles for ever, which reads
        as a quiet news day at every poll."""
        with pytest.raises(ValueError, match="at least one source"):
            CombinedNewsAdapter({})


class TestOneSourceFailingDoesNotLoseTheOthers:
    def test_a_failing_source_still_yields_the_working_one(self) -> None:
        combined = CombinedNewsAdapter(
            {
                "SEBI": StubSource([_article("filing:1")]),
                "Drishti": StubSource(failures=[FeedFailure("news p1", "HTTP 429")]),
            }
        )
        articles = combined.fetch_since(SINCE, ["RELIANCE"])

        assert [a.article_id for a in articles] == ["filing:1"]
        assert combined.recent_failures() != [], "the failure must still be visible"

    def test_failures_name_the_source_they_came_from(self) -> None:
        """ "HTTP 429" alone does not say which source stopped answering, and the
        two have completely different remedies."""
        combined = CombinedNewsAdapter(
            {"Drishti": StubSource(failures=[FeedFailure("news p1", "HTTP 429")])}
        )
        failure = combined.recent_failures()[0]

        assert failure.name == "Drishti: news p1"
        assert failure.reason == "HTTP 429"

    def test_every_source_failed_is_false_when_one_worked(self) -> None:
        combined = CombinedNewsAdapter(
            {
                "SEBI": StubSource([_article("filing:1")]),
                "Drishti": StubSource(failures=[FeedFailure("news", "HTTP 429")]),
            }
        )
        assert combined.every_source_failed() is False

    def test_every_source_failed_is_true_when_none_worked(self) -> None:
        """The distinction the briefing depends on: degraded news is still news,
        no news at all must not be reported as a quiet day (rule 6)."""
        combined = CombinedNewsAdapter(
            {
                "SEBI": StubSource(failures=[FeedFailure("sebi", "TLS")]),
                "Drishti": StubSource(failures=[FeedFailure("news", "HTTP 429")]),
            }
        )
        assert combined.every_source_failed() is True

    def test_no_failures_means_no_alert(self) -> None:
        combined = CombinedNewsAdapter({"SEBI": StubSource([_article("filing:1")])})
        assert combined.recent_failures() == []
        assert combined.every_source_failed() is False
