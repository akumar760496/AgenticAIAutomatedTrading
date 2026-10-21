"""Drishti news adapter.

Every guard here was induced: the test was written to fail first, by feeding the
adapter the shape the API actually returns. The 20-symbol cap and the "N/A"
market-wide tag are both real behaviours observed on the live endpoint on
2026-10-20, not hypotheticals.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from tests.fakes.clock import FrozenClock
from trading_desk.adapters.news.drishti import (
    MAX_ROWS_PER_PAGE,
    MAX_SYMBOLS_PER_CALL,
    DrishtiNewsAdapter,
    DrishtiPolicy,
)
from trading_desk.domain.enums import SourceTier
from trading_desk.domain.news import NewsArticle

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
SINCE = NOW - timedelta(days=30)

UNIVERSE = [
    "ADANIENT",
    "AXISBANK",
    "BAJFINANCE",
    "BHARTIARTL",
    "COALINDIA",
    "HCLTECH",
    "HDFCBANK",
    "HINDALCO",
    "HINDUNILVR",
    "ICICIBANK",
    "INFY",
    "ITC",
    "JSWSTEEL",
    "KOTAKBANK",
    "LT",
    "M&M",
    "MARUTI",
    "NTPC",
    "RELIANCE",
    "SBIN",
    "SUNPHARMA",
    "TATASTEEL",
    "TCS",
    "TITAN",
]


def _policy(**overrides: object) -> DrishtiPolicy:
    defaults: dict[str, object] = {
        "base_url": "https://example.test/v1",
        "min_interval_seconds": 0,
        "max_pages_per_batch": 6,
        "default_publisher_tier": SourceTier.TIER_2,
        "publisher_tiers": {},
    }
    defaults.update(overrides)
    return DrishtiPolicy(**defaults)  # type: ignore[arg-type]


def _adapter(
    handler: Callable[[httpx.Request], httpx.Response],
    policy: DrishtiPolicy | None = None,
) -> DrishtiNewsAdapter:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return DrishtiNewsAdapter(
        "test-key",
        policy or _policy(),
        FrozenClock(NOW),
        http_client=client,
        sleep=lambda _: None,
    )


def _row(**overrides: object) -> dict[str, object]:
    """One row in the shape the live endpoint returned."""
    row: dict[str, object] = {
        "id": "6ad74c2eb0a5464127471650",
        "title": "Reliance posts higher quarterly profit",
        "specific_title": "Reliance Q2 profit rises 12%",
        "summary": "Short summary.",
        "long_summary": "<h2>Results</h2><p>Profit rose 12 percent.</p>",
        "company": "Reliance Industries",
        "symbol": "RELIANCE",
        "sentiment": "positive",
        "source": "Economic Times",
        "article_type": "Financial Performance",
        "link": "https://example.test/article",
        "scrip_code": "500325",
        "date": "2026-10-20T11:06:17+00:00",
    }
    row.update(overrides)
    return row


def _page(rows: list[dict[str, object]], has_next: bool = False) -> httpx.Response:
    return httpx.Response(200, json={"data": rows, "has_next": has_next})


def _single_page(_request: httpx.Request) -> httpx.Response:
    return _page([_row()])


class TestTheTwentySymbolCap:
    """Their `symbols` parameter takes at most 20. Our universe is 24, and
    sending all of them returns HTTP 422 with no rows at all - so the batching
    is what makes the adapter work for the real universe, not an optimisation."""

    def test_twenty_four_symbols_are_split_across_calls(self) -> None:
        seen: list[list[str]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.url.params["symbols"].split(","))
            return _page([])

        _adapter(handler).fetch_since(SINCE, list(UNIVERSE))

        assert len(seen) == 2, "24 symbols must not go out in one call"
        assert all(len(batch) <= MAX_SYMBOLS_PER_CALL for batch in seen)
        sent = [symbol for batch in seen for symbol in batch]
        assert sorted(sent) == sorted(UNIVERSE), "no symbol may be dropped"

    def test_a_single_symbol_still_makes_one_call(self) -> None:
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request.url)
            return _page([])

        _adapter(handler).fetch_since(SINCE, ["RELIANCE"])
        assert len(calls) == 1

    def test_the_page_size_asked_for_is_within_their_limit(self) -> None:
        """limit=100 is rejected as a validation error, so the constant matters."""
        seen = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(int(request.url.params["limit"]))
            return _page([])

        _adapter(handler).fetch_since(SINCE, ["RELIANCE"])
        assert seen == [MAX_ROWS_PER_PAGE]
        assert MAX_ROWS_PER_PAGE <= 50


class TestNotBuyingNewsNobodyAskedFor:
    def test_no_symbols_makes_no_request_at_all(self) -> None:
        """The endpoint is billed per page. An empty universe is a caller bug,
        and answering it by fetching the entire market spends real money."""
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request.url)
            return _page([_row()])

        assert _adapter(handler).fetch_since(SINCE, []) == []
        assert calls == [], "an empty universe must not reach the network"

    def test_a_naive_since_is_refused(self) -> None:
        with pytest.raises(ValueError, match="tz-aware"):
            _adapter(_single_page).fetch_since(datetime(2026, 10, 1), ["RELIANCE"])  # noqa: DTZ001


class TestMarketWideRowsDoNotInventAnInstrument:
    def test_the_literal_na_tag_does_not_become_a_symbol(self) -> None:
        """Observed live: macro and IPO stories come back with symbol "N/A".
        Taken at face value that is an instrument called N/A, linked to every
        macro story in the archive."""
        adapter = _adapter(lambda _r: _page([_row(symbol="N/A", company="N/A")]))
        articles = adapter.fetch_since(SINCE, ["RELIANCE"])

        assert len(articles) == 1, "the article is still kept - it is real news"
        assert articles[0].instrument_symbols == ()

    def test_symbols_we_did_not_ask_about_are_not_attached(self) -> None:
        adapter = _adapter(lambda _r: _page([_row(symbol="RELIANCE,SOMEOTHER")]))
        articles = adapter.fetch_since(SINCE, ["RELIANCE"])
        assert articles[0].instrument_symbols == ("RELIANCE",)


class TestTheColonSeparatedMultiSymbolTag:
    """The bug this exists for. Their *request* parameter takes commas, so commas
    are the obvious guess for the *response* too - but multi-company rows come
    back colon-separated. Splitting on commas alone silently left every such row
    with no instrument attached, and 37% of rows measured on the live endpoint
    on 2026-10-20 were of that form."""

    def test_a_colon_separated_pair_links_both_companies(self) -> None:
        adapter = _adapter(lambda _r: _page([_row(symbol="RELIANCE:ONGC")]))
        articles = adapter.fetch_since(SINCE, ["RELIANCE", "ONGC"])
        assert articles[0].instrument_symbols == ("RELIANCE", "ONGC")

    def test_a_sector_story_naming_eight_banks_links_the_ones_we_hold(self) -> None:
        """Verbatim from the live feed. Under the comma-only split this article
        linked to nothing, which is the opposite of what a catalyst gate wants."""
        tag = "HDFCBANK:KOTAKBANK:ICICIBANK:AXISBANK:IDFCFIRSTB:INDUSINDBK:SBIN:BANKBARODA"
        adapter = _adapter(lambda _r: _page([_row(symbol=tag)]))
        articles = adapter.fetch_since(SINCE, ["HDFCBANK", "KOTAKBANK", "ICICIBANK", "SBIN"])
        assert articles[0].instrument_symbols == (
            "HDFCBANK",
            "KOTAKBANK",
            "ICICIBANK",
            "SBIN",
        )

    def test_an_ampersand_in_a_symbol_survives_the_split(self) -> None:
        """M&M is in our universe and appears inside colon lists."""
        adapter = _adapter(lambda _r: _page([_row(symbol="BAJFINANCE:RELIANCE:M&M:TVSMOTOR")]))
        articles = adapter.fetch_since(SINCE, ["M&M", "RELIANCE"])
        assert articles[0].instrument_symbols == ("RELIANCE", "M&M")

    def test_the_comma_form_still_works(self) -> None:
        adapter = _adapter(lambda _r: _page([_row(symbol="RELIANCE,TCS")]))
        articles = adapter.fetch_since(SINCE, ["RELIANCE", "TCS"])
        assert articles[0].instrument_symbols == ("RELIANCE", "TCS")

    def test_a_repeated_symbol_is_attached_once(self) -> None:
        adapter = _adapter(lambda _r: _page([_row(symbol="TCS:TCS")]))
        articles = adapter.fetch_since(SINCE, ["TCS"])
        assert articles[0].instrument_symbols == ("TCS",)


class TestOneRowStoredOnce:
    def test_a_row_returned_in_both_batches_is_not_duplicated(self) -> None:
        """A market-wide row matches both symbol batches, so without keying by
        id the same story is stored twice and counts as two pieces of evidence."""
        adapter = _adapter(lambda _r: _page([_row(symbol="N/A")]))
        articles = adapter.fetch_since(SINCE, list(UNIVERSE))
        assert len(articles) == 1


class TestPagination:
    def test_it_follows_has_next(self) -> None:
        pages = [
            _page([_row(id="a", date="2026-10-18T10:00:00+00:00")], has_next=True),
            _page([_row(id="b", date="2026-10-19T10:00:00+00:00")], has_next=False),
        ]
        adapter = _adapter(lambda _r: pages.pop(0))
        assert len(adapter.fetch_since(SINCE, ["RELIANCE"])) == 2

    def test_it_stops_at_the_configured_page_cap(self) -> None:
        """has_next is always true on a busy feed. Without a cap this walks the
        whole archive and bills for every page of it."""
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request.url.params["page"])
            return _page([_row(id=f"id{len(calls)}")], has_next=True)

        adapter = _adapter(handler, _policy(max_pages_per_batch=3))
        adapter.fetch_since(SINCE, ["RELIANCE"])
        assert len(calls) == 3


class TestFailuresAreReportedNotSwallowed:
    """CLAUDE.md rule 6. A rate-limited poll and a quiet news day both return an
    empty list; only recent_failures() tells the ingest which happened."""

    def test_a_429_is_recorded(self) -> None:
        adapter = _adapter(lambda _r: httpx.Response(429, text="slow down"))
        assert adapter.fetch_since(SINCE, ["RELIANCE"]) == []
        assert [f.reason for f in adapter.recent_failures()] == ["HTTP 429"]

    def test_a_transport_error_is_recorded(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("no route")

        adapter = _adapter(handler)
        assert adapter.fetch_since(SINCE, ["RELIANCE"]) == []
        assert [f.reason for f in adapter.recent_failures()] == ["ConnectError"]

    def test_html_where_json_was_expected_is_recorded(self) -> None:
        """A proxy or a login page returns 200 with HTML. Parsed loosely that is
        zero articles and no complaint."""
        adapter = _adapter(lambda _r: httpx.Response(200, text="<html>nope</html>"))
        assert adapter.fetch_since(SINCE, ["RELIANCE"]) == []
        assert [f.reason for f in adapter.recent_failures()] == ["response was not JSON"]

    def test_a_json_list_where_an_object_was_expected_is_recorded(self) -> None:
        adapter = _adapter(lambda _r: httpx.Response(200, text=json.dumps([1, 2])))
        assert adapter.fetch_since(SINCE, ["RELIANCE"]) == []
        assert [f.reason for f in adapter.recent_failures()] == ["response was not an object"]

    def test_failures_describe_the_latest_poll_only(self) -> None:
        responses = [httpx.Response(429), _page([_row()])]
        adapter = _adapter(lambda _r: responses.pop(0))

        adapter.fetch_since(SINCE, ["RELIANCE"])
        assert adapter.recent_failures() != []

        adapter.fetch_since(SINCE, ["RELIANCE"])
        assert adapter.recent_failures() == [], "a stale failure would alert for ever"

    def test_one_failing_batch_does_not_lose_the_other(self) -> None:
        responses = [httpx.Response(500), _page([_row()])]
        adapter = _adapter(lambda _r: responses.pop(0))

        articles = adapter.fetch_since(SINCE, list(UNIVERSE))
        assert len(articles) == 1
        assert len(adapter.recent_failures()) == 1


class TestRowsThatCannotBeTrustedAreDropped:
    def test_an_unparseable_date_drops_the_row(self) -> None:
        """Defaulting to "now" would file an old story into today's session and
        make it look like a fresh catalyst."""
        adapter = _adapter(lambda _r: _page([_row(date="yesterday-ish")]))
        assert adapter.fetch_since(SINCE, ["RELIANCE"]) == []

    def test_a_date_without_a_timezone_drops_the_row(self) -> None:
        adapter = _adapter(lambda _r: _page([_row(date="2026-10-20T11:06:17")]))
        assert adapter.fetch_since(SINCE, ["RELIANCE"]) == []

    def test_a_row_with_no_headline_drops(self) -> None:
        adapter = _adapter(lambda _r: _page([_row(title="", specific_title="")]))
        assert adapter.fetch_since(SINCE, ["RELIANCE"]) == []

    def test_a_row_with_no_id_drops(self) -> None:
        adapter = _adapter(lambda _r: _page([_row(id="")]))
        assert adapter.fetch_since(SINCE, ["RELIANCE"]) == []


class TestTheStoredArticle:
    def test_markup_becomes_readable_text(self) -> None:
        """The credibility scorer reads wording. Left as HTML, "<h2>Results</h2>"
        runs the heading into the first sentence."""
        adapter = _adapter(lambda _r: _page([_row()]))
        body = adapter.fetch_since(SINCE, ["RELIANCE"])[0].body

        assert "<" not in body
        assert "Results" in body
        assert "ResultsProfit" not in body, "a tag boundary must become a break"

    def test_entities_are_unescaped(self) -> None:
        adapter = _adapter(lambda _r: _page([_row(long_summary="<p>M&amp;M gains</p>")]))
        assert "M&M gains" in adapter.fetch_since(SINCE, ["RELIANCE"])[0].body

    def test_the_summary_is_used_when_there_is_no_long_summary(self) -> None:
        adapter = _adapter(lambda _r: _page([_row(long_summary="")]))
        assert adapter.fetch_since(SINCE, ["RELIANCE"])[0].body == "Short summary."

    def test_a_publisher_is_tier_2_unless_config_says_otherwise(self) -> None:
        """What we store is Drishti's summary of the article, not the article.
        Inheriting the newspaper's standing would loosen Setup A's credibility
        gate on the strength of an AI precis."""
        adapter = _adapter(lambda _r: _page([_row()]))
        assert adapter.fetch_since(SINCE, ["RELIANCE"])[0].source_tier is SourceTier.TIER_2

    def test_config_can_raise_a_named_publisher(self) -> None:
        policy = _policy(publisher_tiers={"Economic Times": SourceTier.TIER_1})
        adapter = _adapter(lambda _r: _page([_row()]), policy)
        assert adapter.fetch_since(SINCE, ["RELIANCE"])[0].source_tier is SourceTier.TIER_1

    def test_the_specific_title_is_preferred_as_the_headline(self) -> None:
        adapter = _adapter(lambda _r: _page([_row()]))
        assert (
            adapter.fetch_since(SINCE, ["RELIANCE"])[0].headline == "Reliance Q2 profit rises 12%"
        )

    def test_the_id_is_namespaced_so_two_sources_cannot_collide(self) -> None:
        adapter = _adapter(lambda _r: _page([_row()]))
        assert adapter.fetch_since(SINCE, ["RELIANCE"])[0].article_id.startswith("drishti:")

    def test_ingested_at_comes_from_the_injected_clock(self) -> None:
        adapter = _adapter(lambda _r: _page([_row()]))
        assert adapter.fetch_since(SINCE, ["RELIANCE"])[0].ingested_at == NOW

    def test_articles_come_back_oldest_first(self) -> None:
        rows = [
            _row(id="new", date="2026-10-19T10:00:00+00:00"),
            _row(id="old", date="2026-10-01T10:00:00+00:00"),
        ]
        adapter = _adapter(lambda _r: _page(rows))
        articles = adapter.fetch_since(SINCE, ["RELIANCE"])
        assert [a.article_id for a in articles] == ["drishti:old", "drishti:new"]

    def test_it_satisfies_the_port(self) -> None:
        adapter = _adapter(lambda _r: _page([_row()]))
        articles = adapter.fetch_since(SINCE, ["RELIANCE"])
        assert all(isinstance(a, NewsArticle) for a in articles)


class TestTheRequestItself:
    def test_the_api_key_goes_in_the_header_and_not_the_query(self) -> None:
        """Their spec also accepts ?api_key=. A key in a URL lands in access logs
        and in any error we might print (CLAUDE.md rule 8)."""
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return _page([])

        _adapter(handler).fetch_since(SINCE, ["RELIANCE"])
        assert seen[0].headers["X-API-Key"] == "test-key"
        assert "api_key" not in str(seen[0].url)

    def test_the_since_date_is_sent_as_the_from_bound(self) -> None:
        seen = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.url.params["from"])
            return _page([])

        _adapter(handler).fetch_since(SINCE, ["RELIANCE"])
        assert seen == ["2026-09-20"]
