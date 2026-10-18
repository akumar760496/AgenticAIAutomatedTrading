"""Article ingestion: link, group, store.

Grouping runs after entity resolution because it is partitioned by instrument.
Two stories about different companies must never merge however similar the text -
measured at 0.84 for "TCS wins deal" against "Infosys wins deal".
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from tests.fakes import FakeNewsStore, FakeNotifier, FrozenClock
from trading_desk.application.news_ingest import STALE_FEED_DAYS, ArticleIngest
from trading_desk.domain.entities import AliasTable
from trading_desk.domain.enums import SourceTier
from trading_desk.domain.news import FeedFailure, NewsArticle
from trading_desk.ports.notifier import AlertLevel

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)

ALIASES = AliasTable(
    aliases={
        "RELIANCE": ("Reliance Industries",),
        "TCS": ("Tata Consultancy Services",),
    },
    confusable=("Tata Motors",),
)


def _article(
    article_id: str, headline: str, symbol_hint: str = "", hours_ago: int = 1
) -> NewsArticle:
    return NewsArticle(
        article_id=article_id,
        headline=headline,
        body=symbol_hint,
        url=f"https://example.test/{article_id}",
        publisher="Test Daily",
        source_tier=SourceTier.TIER_1,
        published_at=NOW - timedelta(hours=hours_ago),
        ingested_at=NOW,
    )


class FakeNewsFeed:
    def __init__(self, articles: list[NewsArticle]) -> None:
        self._articles = articles
        self.fail_next_fetch = False
        self.failures: list[FeedFailure] = []

    def recent_failures(self) -> list[FeedFailure]:
        return list(self.failures)

    def fetch_since(
        self,
        since: datetime,
        symbols: list[str],  # noqa: ARG002 - the port's shape; RSS carries no symbols
    ) -> list[NewsArticle]:
        if self.fail_next_fetch:
            self.fail_next_fetch = False
            raise ConnectionError("Simulated feed failure")
        return [a for a in self._articles if a.published_at > since]


def _article_ingest(
    feed: FakeNewsFeed, store: FakeNewsStore, notifier: FakeNotifier
) -> ArticleIngest:
    return ArticleIngest(
        news=feed,
        store=store,
        notifier=notifier,
        clock=FrozenClock(NOW),
        aliases=ALIASES,
        sources=(("Test Daily", "TIER_1", "a reason"),),
    )


class TestArticleIngest:
    def test_links_articles_to_instruments(self) -> None:
        feed = FakeNewsFeed([_article("1", "Reliance Industries announces a demerger")])
        store, notifier = FakeNewsStore(), FakeNotifier()

        report = _article_ingest(feed, store, notifier).run()

        assert report.linked == 1
        assert store.articles["1"].instrument_symbols == ("RELIANCE",)

    def test_an_unlinked_article_is_still_stored(self) -> None:
        feed = FakeNewsFeed([_article("1", "Markets close higher on global cues")])
        store, notifier = FakeNewsStore(), FakeNotifier()

        report = _article_ingest(feed, store, notifier).run()

        assert report.inserted == 1
        assert report.linked == 0

    def test_syndicated_copies_share_one_event_key(self) -> None:
        feed = FakeNewsFeed(
            [
                _article("a", "Reliance Industries Q2 profit rises 12% beating estimates"),
                _article("b", "Reliance Industries Q2 profit climbs 12%, ahead of estimates"),
            ]
        )
        store, notifier = FakeNewsStore(), FakeNotifier()

        report = _article_ingest(feed, store, notifier).run()

        assert report.fetched == 2
        assert report.events == 1
        assert report.syndicated_events == 1
        assert store.articles["a"].event_key == store.articles["b"].event_key

    def test_different_companies_never_share_an_event(self) -> None:
        """Even when the text is nearly identical."""
        feed = FakeNewsFeed(
            [
                _article("a", "Reliance Industries wins a $1 billion contract in Europe"),
                _article("b", "Tata Consultancy Services wins a $1 billion contract in Europe"),
            ]
        )
        store, notifier = FakeNewsStore(), FakeNotifier()

        report = _article_ingest(feed, store, notifier).run()

        assert report.events == 2
        assert store.articles["a"].event_key != store.articles["b"].event_key

    def test_a_second_poll_inserts_nothing(self) -> None:
        feed = FakeNewsFeed([_article("1", "Reliance Industries announces a demerger")])
        store, notifier = FakeNewsStore(), FakeNotifier()
        ingest = _article_ingest(feed, store, notifier)

        first, second = ingest.run(), ingest.run()

        assert (first.inserted, second.inserted) == (1, 0)

    def test_a_feed_outage_is_reported_not_raised(self) -> None:
        feed = FakeNewsFeed([_article("1", "Reliance Industries news")])
        feed.fail_next_fetch = True
        store, notifier = FakeNewsStore(), FakeNotifier()

        report = _article_ingest(feed, store, notifier).run()

        assert not report.succeeded
        assert notifier.alerts[-1][0] is AlertLevel.WARNING

    def test_a_feed_that_publishes_nothing_recent_is_flagged(self) -> None:
        """A dead feed answers 200 with valid XML and looks like a quiet day."""
        stale_hours = (STALE_FEED_DAYS + 1) * 24
        feed = FakeNewsFeed([_article("1", "Old news", hours_ago=stale_hours)])
        store, notifier = FakeNewsStore(), FakeNotifier()

        report = _article_ingest(feed, store, notifier).run(
            lookback=timedelta(days=STALE_FEED_DAYS + 3)
        )

        assert report.stale_feeds == ["Test Daily"]

    def test_source_tiers_are_recorded_with_their_reason(self) -> None:
        """FR-3.6: a tier with no stated reason is a number nobody can argue with."""
        feed = FakeNewsFeed([_article("1", "Reliance Industries news")])
        store, notifier = FakeNewsStore(), FakeNotifier()

        _article_ingest(feed, store, notifier).run()

        assert store.sources["Test Daily"] == ("TIER_1", "a reason")


class TestFeedFailuresAreSurfaced:
    """A source going dark must not read as a quiet news day (rule 6)."""

    def test_an_unreadable_feed_reaches_the_report_and_an_alert(self) -> None:
        feed = FakeNewsFeed([_article("1", "Markets close higher")])
        feed.failures = [FeedFailure("SEBI", "HTTP 403")]
        store, notifier = FakeNewsStore(), FakeNotifier()

        report = _article_ingest(feed, store, notifier).run()

        assert [item.name for item in report.failed_feeds] == ["SEBI"]
        assert any(
            level is AlertLevel.WARNING and "SEBI" in message for level, message in notifier.alerts
        )
        assert "1 feed(s) unreadable" in report.summary()

    def test_a_healthy_poll_says_nothing_about_failures(self) -> None:
        feed = FakeNewsFeed([_article("1", "Markets close higher")])
        store, notifier = FakeNewsStore(), FakeNotifier()

        report = _article_ingest(feed, store, notifier).run()

        assert report.failed_feeds == []
        assert "unreadable" not in report.summary()
