"""The RSS adapter, against canned responses.

Three behaviours here are regressions waiting to happen, because each one fails
*silently* - the adapter returns an empty list and the poll looks healthy.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import httpx

from tests.fakes import FrozenClock
from trading_desk.adapters.news.rss import FeedSource, RssNewsAdapter, SourcePolicy
from trading_desk.domain.enums import SourceTier
from trading_desk.domain.feed_dates import FeedDateFormat

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)

DATE_ONLY_FEED = """<?xml version="1.0"?>
<rss version="2.0"><channel>
  <item>
    <title>Appeal No. 7107 of 2026</title>
    <description>An order</description>
    <link>https://www.sebi.gov.in/orders/one.html</link>
    <pubDate>18 Oct, 2026 +0530</pubDate>
  </item>
</channel></rss>
"""


def _policy(
    date_format: FeedDateFormat, url: str = "https://example.test/feed.xml"
) -> SourcePolicy:
    return SourcePolicy(
        user_agent="trading-desk/0.1 (test)",
        min_interval_seconds=0,
        feeds=(
            FeedSource(
                name="Regulator",
                url=url,
                tier=SourceTier.EXCHANGE,
                tier_reason="test",
                date_format=date_format,
            ),
        ),
    )


def _adapter(
    handler: Callable[[httpx.Request], httpx.Response],
    date_format: FeedDateFormat = FeedDateFormat.DATE_ONLY,
) -> RssNewsAdapter:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return RssNewsAdapter(
        _policy(date_format), FrozenClock(NOW), http_client=client, sleep=lambda _: None
    )


def _ok(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, text=DATE_ONLY_FEED)


class TestDateOnlyWindowing:
    def test_a_midday_poll_still_sees_todays_items(self) -> None:
        """The bug this exists for: SEBI stamps every item midnight. Comparing
        instants, a 12:00 poll asking for "since 10:00" drops the whole feed,
        because midnight is before 10:00."""
        adapter = _adapter(_ok)
        since = NOW - timedelta(hours=2)

        articles = adapter.fetch_since(since, [])

        assert len(articles) == 1
        assert articles[0].headline == "Appeal No. 7107 of 2026"

    def test_the_same_feed_read_as_rfc822_yields_nothing(self) -> None:
        """Induces the original fault: with the wrong format declared the items
        are unparseable and the adapter goes quietly empty."""
        adapter = _adapter(_ok, date_format=FeedDateFormat.RFC822)

        assert adapter.fetch_since(NOW - timedelta(hours=2), []) == []

    def test_an_older_day_is_still_excluded(self) -> None:
        """Windowing by day must not become no windowing at all."""
        adapter = _adapter(_ok)

        assert adapter.fetch_since(NOW + timedelta(days=2), []) == []


class TestFailuresAreReported:
    def test_a_refusal_is_recorded_not_swallowed(self) -> None:
        adapter = _adapter(lambda _r: httpx.Response(403, text="no"))

        articles = adapter.fetch_since(NOW - timedelta(days=1), [])

        assert articles == []
        assert [f.reason for f in adapter.recent_failures()] == ["HTTP 403"]

    def test_a_transport_error_is_recorded(self) -> None:
        """A TLS failure looked exactly like a quiet feed until this existed."""

        def explode(_request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("certificate verify failed")

        adapter = _adapter(explode)
        adapter.fetch_since(NOW - timedelta(days=1), [])

        assert [f.reason for f in adapter.recent_failures()] == ["ConnectError"]

    def test_items_present_but_unparseable_is_itself_a_failure(self) -> None:
        """A feed that changes its date format must not read as a quiet day."""
        adapter = _adapter(_ok, date_format=FeedDateFormat.RFC822)
        adapter.fetch_since(NOW - timedelta(days=1), [])

        assert [f.reason for f in adapter.recent_failures()] == ["items present but none parseable"]

    def test_a_healthy_poll_reports_no_failure(self) -> None:
        adapter = _adapter(_ok)
        adapter.fetch_since(NOW - timedelta(days=1), [])

        assert adapter.recent_failures() == []

    def test_failures_describe_the_latest_poll_only(self) -> None:
        responses = [httpx.Response(403), httpx.Response(200, text=DATE_ONLY_FEED)]
        adapter = _adapter(lambda _r: responses.pop(0))

        adapter.fetch_since(NOW - timedelta(days=1), [])
        adapter.fetch_since(NOW - timedelta(days=1), [])

        assert adapter.recent_failures() == []
