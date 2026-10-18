"""Regulator RSS, read the way the publishers permit.

**This adapter never fetches an article page.** It stores only what the feed
itself publishes - headline, link, publisher, timestamp, and the summary the
publisher chose to syndicate. Fetching the page body is where terms generally
prohibit automated retrieval and storage, and declining to cross that line is a
deliberate constraint, not an oversight.

**The client identifies itself honestly** - name, that it is automated, and a URL
to complain to. If a source answers an honest identifier with a refusal, it is
dropped rather than disguised: Business Standard returns 403 to this User-Agent
while serving a spoofed browser one, and is recorded as excluded in
`config/news_sources.yaml` for that reason.

Three things this adapter has to get right, each of which failed silently first:

1. **The trust store.** httpx 0.28 verifies against the system OpenSSL store,
   which on this machine holds 128 roots and not Sectigo Root R46 - the root
   SEBI's certificate chains to. Every SEBI request raised
   CERTIFICATE_VERIFY_FAILED, which the per-feed error handler turned into an
   empty list. `certifi` carries the root, so it is named explicitly rather than
   left to whatever the host happens to trust.
2. **The date format**, which is declared per feed. See `domain.feed_dates`.
3. **Failures are reported, not swallowed.** A feed that 403s or fails TLS must
   not read as a quiet news day (CLAUDE.md rule 6). One bad feed still must not
   end the poll, so the failure is recorded and the other feeds are read.

Politeness beyond compliance: conditional requests via ETag and Last-Modified, so
an unchanged feed costs the publisher a 304 rather than a full body, and a
minimum interval between requests to the same host.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

import certifi
import httpx
import yaml

from trading_desk.domain.enums import SourceTier
from trading_desk.domain.feed_dates import FeedDateFormat, parse_published
from trading_desk.domain.news import FeedFailure, NewsArticle
from trading_desk.domain.timezones import to_ist
from trading_desk.ports.clock import Clock

DEFAULT_SOURCES_PATH = Path("config/news_sources.yaml")
DEFAULT_TIMEOUT_SECONDS = 20.0


@dataclass(frozen=True)
class FeedSource:
    """One RSS feed, with the reason it is trusted at its tier."""

    name: str
    url: str
    tier: SourceTier
    tier_reason: str
    date_format: FeedDateFormat = FeedDateFormat.RFC822


@dataclass(frozen=True)
class SourcePolicy:
    """Everything config says about how to read feeds."""

    user_agent: str
    min_interval_seconds: float
    feeds: tuple[FeedSource, ...]


def load_source_policy(path: Path = DEFAULT_SOURCES_PATH) -> SourcePolicy:
    if not path.is_file():
        raise FileNotFoundError(f"News sources not found at {path}")

    raw = yaml.safe_load(path.read_text()) or {}
    feeds = tuple(
        FeedSource(
            name=str(entry["name"]),
            url=str(entry["url"]),
            tier=SourceTier(entry["tier"]),
            tier_reason=str(entry.get("tier_reason", "")).strip(),
            date_format=FeedDateFormat(entry.get("date_format", FeedDateFormat.RFC822)),
        )
        for entry in raw.get("feeds", [])
    )
    return SourcePolicy(
        user_agent=str(raw["user_agent"]),
        min_interval_seconds=float(raw.get("min_interval_seconds", 5)),
        feeds=feeds,
    )


class RssNewsAdapter:
    """Implements NewsPort against configured RSS feeds."""

    def __init__(
        self,
        policy: SourcePolicy,
        clock: Clock,
        http_client: httpx.Client | None = None,
        sleep: Any = time.sleep,
    ) -> None:
        self._policy = policy
        self._clock = clock
        self._sleep = sleep
        self._http = http_client or httpx.Client(
            timeout=DEFAULT_TIMEOUT_SECONDS,
            headers={"User-Agent": policy.user_agent, "Accept": "application/rss+xml, */*"},
            follow_redirects=True,
            # See the module docstring: the system store is missing the root
            # SEBI chains to, and the failure is invisible without this.
            verify=certifi.where(),
        )
        # Per-feed cache validators, so an unchanged feed costs a 304.
        self._etags: dict[str, str] = {}
        self._last_modified: dict[str, str] = {}
        self._last_request_at: float | None = None
        self._failures: list[FeedFailure] = []

    def fetch_since(
        self,
        since: datetime,
        symbols: list[str],  # noqa: ARG002 - see docstring: filtering here would lose articles
    ) -> list[NewsArticle]:
        """Articles published after `since`, from every configured feed.

        `symbols` is accepted for the port's shape but not used to filter: an RSS
        feed carries no instrument tags, so linking is entity resolution's job
        and happens after ingestion. Filtering here would discard articles whose
        company is named only in the body.
        """
        if since.tzinfo is None:
            raise ValueError("since must be tz-aware")

        self._failures = []
        articles: list[NewsArticle] = []
        for feed in self._policy.feeds:
            articles.extend(self._fetch_feed(feed, since))
        return sorted(articles, key=lambda article: article.published_at)

    def recent_failures(self) -> list[FeedFailure]:
        """Feeds that could not be read on the last `fetch_since`.

        Read by the ingest so a dead feed is alerted on rather than mistaken for
        silence. Reset at the start of every poll, so it describes that poll.
        """
        return list(self._failures)

    def _fetch_feed(self, feed: FeedSource, since: datetime) -> list[NewsArticle]:
        """One feed. A failing feed is recorded and skipped, never fatal."""
        self._wait_for_politeness()
        received_at = self._clock.utc_now()

        headers = {}
        if etag := self._etags.get(feed.url):
            headers["If-None-Match"] = etag
        if modified := self._last_modified.get(feed.url):
            headers["If-Modified-Since"] = modified

        try:
            response = self._http.get(feed.url, headers=headers)
        except httpx.HTTPError as error:
            self._failures.append(FeedFailure(feed.name, type(error).__name__))
            return []

        if response.status_code == httpx.codes.NOT_MODIFIED:
            return []
        if response.status_code != httpx.codes.OK:
            # Includes 403: a refusal to an honest client is an answer, not an
            # obstacle to work around. See config/news_sources.yaml.
            self._failures.append(FeedFailure(feed.name, f"HTTP {response.status_code}"))
            return []

        if tag := response.headers.get("ETag"):
            self._etags[feed.url] = tag
        if modified := response.headers.get("Last-Modified"):
            self._last_modified[feed.url] = modified

        articles = _parse_feed(response.text, feed, received_at, since)
        if not articles and _item_count(response.text) > 0:
            # The feed had items and none survived parsing - almost always a
            # changed date format. Worth an alert; it is indistinguishable from
            # a quiet feed otherwise.
            self._failures.append(FeedFailure(feed.name, "items present but none parseable"))
        return articles

    def _wait_for_politeness(self) -> None:
        if self._last_request_at is not None:
            elapsed = time.monotonic() - self._last_request_at
            remaining = self._policy.min_interval_seconds - elapsed
            if remaining > 0:
                self._sleep(remaining)
        self._last_request_at = time.monotonic()

    def close(self) -> None:
        self._http.close()


def _text(element: Any, tag: str) -> str:
    found = element.find(tag)
    if found is None or found.text is None:
        return ""
    return str(found.text).strip()


def _item_count(xml: str) -> int:
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError:
        return 0
    return len(list(root.iter("item")))


def _is_newer_than(published: datetime, since: datetime, date_format: FeedDateFormat) -> bool:
    """Whether an item falls inside the poll window.

    A date-only feed stamps every item midnight, so comparing instants would
    drop everything it publishes later the same day - on the second poll of a
    day, that is the whole feed. Comparing calendar days instead re-offers
    items already seen, which costs nothing: the store is ON CONFLICT DO
    NOTHING on the item's link.
    """
    if date_format.is_date_only:
        return to_ist(published).date() >= to_ist(since).date()
    return published > since


def _parse_feed(
    xml: str, feed: FeedSource, received_at: datetime, since: datetime
) -> list[NewsArticle]:
    """Map RSS items to NewsArticles, skipping anything unusable.

    A malformed feed yields nothing rather than raising: one publisher serving
    broken XML must not stop the others.
    """
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError:
        return []

    articles = []
    for item in root.iter("item"):
        published = parse_published(_text(item, "pubDate"), feed.date_format)
        if published is None or not _is_newer_than(published, since, feed.date_format):
            continue

        headline = _text(item, "title")
        link = _text(item, "link")
        if not headline or not link:
            continue

        articles.append(
            NewsArticle(
                # The link is the publisher's own stable identifier for the
                # story. A hash of the text would change when a headline is
                # edited, re-ingesting the same story as new.
                article_id=link,
                headline=headline,
                # The summary the publisher chose to syndicate. Not the article.
                body=_text(item, "description"),
                url=link,
                publisher=feed.name,
                source_tier=feed.tier,
                published_at=published,
                ingested_at=received_at,
            )
        )
    return articles
