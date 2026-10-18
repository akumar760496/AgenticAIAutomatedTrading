"""Polls exchange filings, links them to instruments, and stores them.

The ordering matters and is not obvious: **store everything, link what we can.**
A filing for a company outside the universe is still stored. It costs almost
nothing, and the universe changes - step 1.2 re-ranks it monthly, so today's
irrelevant name is next month's holding, and history that was never collected
cannot be collected later.

Two numbers come back that are easy to confuse and must not be:

- `fetched` is what the exchange had in the window.
- `inserted` is what was new to us. On a healthy poll with an overlapping window
  these differ, and that is correct, not a bug.

A poll that covers only "since the last one" is a poll that loses anything
arriving late or out of order, so the window deliberately overlaps and the store
absorbs the repeats.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from trading_desk.domain.entities import AliasTable, resolve_entities
from trading_desk.domain.news import FeedFailure, Filing, NewsArticle
from trading_desk.domain.similarity import SimilarDocument, group_near_duplicates
from trading_desk.domain.untrusted import inspect
from trading_desk.ports.clock import Clock
from trading_desk.ports.news import FilingsPort, NewsPort
from trading_desk.ports.news_store import NewsStorePort
from trading_desk.ports.notifier import AlertLevel, NotifierPort

# How far back each poll reaches, regardless of when the last one ran. Wider
# than any sane interval, so a missed poll or a late filing is still caught.
DEFAULT_LOOKBACK = timedelta(days=2)

# A feed whose newest article is older than this has stopped publishing. Not an
# error - the request succeeds and the XML is valid - which is why it needs
# measuring: silence from a dead feed reads as a quiet news day. Moneycontrol's
# feed was two and a half years stale when checked and answered 200 throughout.
#
# An application rule, not an adapter detail: how fresh data must be to be worth
# acting on is a decision about the research, not about HTTP. import-linter
# caught it living in the adapter.
STALE_FEED_DAYS = 3


@dataclass(frozen=True)
class SuspiciousFiling:
    """A filing whose body reads like a planted instruction (FR-3.9)."""

    filing_id: str
    symbol: str
    suspicions: tuple[str, ...]


@dataclass
class IngestReport:
    """What one poll found, stored, and noticed."""

    fetched: int = 0
    inserted: int = 0
    universe_matched: int = 0
    suspicious: list[SuspiciousFiling] = field(default_factory=list)
    error: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.error is None

    def summary(self) -> str:
        if self.error is not None:
            return f"ingest failed: {self.error}"
        return (
            f"fetched {self.fetched}, inserted {self.inserted} new, "
            f"{self.universe_matched} for universe names, "
            f"{len(self.suspicious)} flagged"
        )


class FilingsIngest:
    """One poll of the exchange announcement feed."""

    def __init__(
        self,
        filings: FilingsPort,
        store: NewsStorePort,
        notifier: NotifierPort,
        clock: Clock,
        aliases: AliasTable,
    ) -> None:
        self._filings = filings
        self._store = store
        self._notifier = notifier
        self._clock = clock
        self._aliases = aliases

    def run(self, lookback: timedelta = DEFAULT_LOOKBACK) -> IngestReport:
        since = self._clock.utc_now() - lookback

        try:
            # No symbol filter: everything in the window is stored, because the
            # universe changes and uncollected history cannot be recovered.
            fetched = self._filings.fetch_since(since, [])
        except Exception as error:  # a feed outage must not crash the scheduler
            report = IngestReport(error=f"{type(error).__name__}: {str(error)[:150]}")
            self._notifier.send_alert(AlertLevel.WARNING, report.summary())
            return report

        report = IngestReport(fetched=len(fetched))
        report.inserted = self._store.save_filings(fetched)
        report.universe_matched = sum(1 for filing in fetched if self._matches_universe(filing))
        report.suspicious = [
            SuspiciousFiling(
                filing_id=filing.filing_id,
                symbol=filing.symbol,
                suspicions=inspect(filing.body).suspicions,
            )
            for filing in fetched
            if inspect(filing.body).is_suspicious
        ]

        self._report(report)
        return report

    def _matches_universe(self, filing: Filing) -> bool:
        """Whether this filing concerns a universe name.

        Checks the exchange's own symbol first, which is authoritative, and falls
        back to resolving the prose. The alias path matters for a filing whose
        subject names a subsidiary or a counterparty.
        """
        if filing.symbol in self._aliases.aliases:
            return True
        text = f"{filing.subject} {filing.body}"
        return bool(resolve_entities(text, self._aliases))

    def _report(self, report: IngestReport) -> None:
        if report.suspicious:
            # Flagged, not filtered: the rows are stored either way. A story
            # someone tried to poison is itself information (FR-3.9).
            self._notifier.send_alert(
                AlertLevel.WARNING,
                "Possible planted instructions in: "
                + ", ".join(f"{item.symbol}/{item.filing_id}" for item in report.suspicious),
            )
            return
        self._notifier.send_alert(AlertLevel.INFO, f"Filings ingest: {report.summary()}")


def load_alias_table(raw: dict[str, Any]) -> AliasTable:
    """Build an AliasTable from parsed YAML.

    Takes the parsed mapping rather than a path, so the pure table can be built
    in a test without touching the filesystem.
    """
    aliases: dict[str, tuple[str, ...]] = {}
    for symbol, names in (raw.get("aliases") or {}).items():
        aliases[str(symbol)] = tuple(str(name) for name in names)

    confusable = tuple(str(name) for name in (raw.get("confusable") or []))
    return AliasTable(aliases=aliases, confusable=confusable)


def load_aliases_from_file(path: Path = Path("config/aliases.yaml")) -> AliasTable:
    """Load the shipped alias table."""
    if not path.is_file():
        raise FileNotFoundError(f"Alias table not found at {path}")
    return load_alias_table(yaml.safe_load(path.read_text()) or {})


@dataclass
class ArticleIngestReport:
    """What one news poll found, linked, grouped and stored."""

    fetched: int = 0
    inserted: int = 0
    linked: int = 0
    events: int = 0
    syndicated_events: int = 0
    stale_feeds: list[str] = field(default_factory=list)
    failed_feeds: list[FeedFailure] = field(default_factory=list)
    suspicious: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.error is None

    def summary(self) -> str:
        if self.error is not None:
            return f"news ingest failed: {self.error}"
        failed = f", {len(self.failed_feeds)} feed(s) unreadable" if self.failed_feeds else ""
        return (
            f"fetched {self.fetched}, inserted {self.inserted} new, "
            f"{self.linked} linked to instruments, "
            f"{self.events} distinct events ({self.syndicated_events} multi-source)"
            f"{failed}"
        )


class ArticleIngest:
    """One poll of the configured RSS feeds.

    The order is: fetch, resolve entities, group, store. Grouping must come after
    entity resolution because the grouping is partitioned by instrument - two
    stories about different companies must never merge no matter how similar the
    text, and measured similarity between "TCS wins deal" and "Infosys wins deal"
    was 0.84 (see domain.similarity).
    """

    def __init__(
        self,
        *,
        news: NewsPort,
        store: NewsStorePort,
        notifier: NotifierPort,
        clock: Clock,
        aliases: AliasTable,
        sources: Sequence[tuple[str, str, str]] = (),
    ) -> None:
        self._news = news
        self._store = store
        self._notifier = notifier
        self._clock = clock
        self._aliases = aliases
        self._sources = sources

    def run(self, lookback: timedelta = DEFAULT_LOOKBACK) -> ArticleIngestReport:
        now = self._clock.utc_now()
        since = now - lookback

        # Recorded every poll rather than once at setup, so the table cannot
        # drift from config/news_sources.yaml after a tier is re-argued.
        self._store.save_sources(self._sources)

        try:
            fetched = self._news.fetch_since(since, [])
        except Exception as error:  # a feed outage must not crash the scheduler
            report = ArticleIngestReport(error=f"{type(error).__name__}: {str(error)[:150]}")
            self._notifier.send_alert(AlertLevel.WARNING, report.summary())
            return report

        linked = [self._link(article) for article in fetched]
        grouped = self._group(linked)

        report = ArticleIngestReport(fetched=len(grouped))
        report.inserted = self._store.save_articles(grouped)
        report.linked = sum(1 for article in grouped if article.instrument_symbols)
        keys = {article.event_key for article in grouped if article.event_key}
        report.events = len(keys)
        report.syndicated_events = _count_syndicated(grouped)
        report.stale_feeds = _stale_feeds(grouped, now)
        report.failed_feeds = self._news.recent_failures()
        report.suspicious = [
            article.article_id for article in grouped if inspect(article.body).is_suspicious
        ]

        self._report(report)
        return report

    def _link(self, article: NewsArticle) -> NewsArticle:
        """Attach the instruments the article names, by alias not substring."""
        text = f"{article.headline} {article.body}"
        symbols = tuple(sorted({match.symbol for match in resolve_entities(text, self._aliases)}))
        return replace(article, instrument_symbols=symbols)

    def _group(self, articles: list[NewsArticle]) -> list[NewsArticle]:
        """Assign an event_key so syndicated copies share one event."""
        documents = [
            SimilarDocument(
                document_id=article.article_id,
                text=f"{article.headline} {article.body}",
                symbols=frozenset(article.instrument_symbols),
            )
            for article in articles
        ]

        key_for: dict[str, str] = {}
        for group in group_near_duplicates(documents):
            for member in group.member_ids:
                key_for[member] = group.representative_id

        return [replace(article, event_key=key_for.get(article.article_id)) for article in articles]

    def _report(self, report: ArticleIngestReport) -> None:
        if report.failed_feeds:
            # A source going dark is not a quiet news day. Said first, because
            # everything below describes a poll that was missing a feed.
            self._notifier.send_alert(
                AlertLevel.WARNING,
                "Feeds unreadable this poll: "
                + ", ".join(f"{item.name} ({item.reason})" for item in report.failed_feeds),
            )
        if report.stale_feeds:
            self._notifier.send_alert(
                AlertLevel.WARNING,
                "Feeds publishing nothing recent: " + ", ".join(report.stale_feeds),
            )
        if report.suspicious:
            self._notifier.send_alert(
                AlertLevel.WARNING,
                f"Possible planted instructions in {len(report.suspicious)} article(s)",
            )
            return
        self._notifier.send_alert(AlertLevel.INFO, f"News ingest: {report.summary()}")


def _count_syndicated(articles: list[NewsArticle]) -> int:
    """Events carried by more than one article."""
    counts: dict[str, int] = {}
    for article in articles:
        if article.event_key:
            counts[article.event_key] = counts.get(article.event_key, 0) + 1
    return sum(1 for total in counts.values() if total > 1)


def _stale_feeds(articles: list[NewsArticle], now: datetime) -> list[str]:
    """Publishers whose newest article is older than STALE_FEED_DAYS.

    A feed can answer 200 with valid XML and still have stopped publishing -
    Moneycontrol's was two and a half years stale when checked. Silence from a
    dead feed is indistinguishable from a quiet news day unless it is measured.
    """
    newest: dict[str, datetime] = {}
    for article in articles:
        current = newest.get(article.publisher)
        if current is None or article.published_at > current:
            newest[article.publisher] = article.published_at

    cutoff = now - timedelta(days=STALE_FEED_DAYS)
    return sorted(name for name, when in newest.items() if when < cutoff)
