"""Drishti (Manasija) news, read through their paid API.

**What this stores is Drishti's summary, not the publisher's article.** Their
`/news` response carries a headline, their own `summary` and `long_summary`, the
publisher's name and a link to the original page. We never fetch that page. That
is the same line `rss.py` draws on purpose: store what the source syndicates,
never retrieve the body it did not offer.

It follows that **every article from here is reporting at best, and a derived
summary of reporting at that**. An AI-written precis of an Economic Times piece
is not an Economic Times piece, so the credibility tier comes from config and
defaults to TIER_2 rather than inheriting the newspaper's standing. Nothing from
this adapter is ever EXCHANGE tier - exchange filings are a separate product we
have deliberately not wired (see DEVLOG, 2026-10-20).

Three API facts the caller should not have to know, so they live here:

1. **`symbols` accepts at most 20 per call** and our universe is 24, so requests
   are batched and the pages merged. Getting this wrong returns HTTP 422, not a
   short list.
2. **`limit` maxes out at 50.** Asking for 100 is a validation error.
3. **The sandbox plan allows 10 requests a minute**, so a minimum interval
   between calls is enforced rather than left to the caller's goodwill.

Unlike `rss.py` this adapter *does* use the `symbols` argument: Drishti tags each
item with the company it concerns, and market-wide items carry `"N/A"`. Filtering
server-side is what makes the call cheap, and news is billed per page.

**Compliance note.** Their Terms permit storage for our licensed use and we own
what we derive, but ISS-043's question - whether any of it was "aggregated from
public sources", i.e. scraped from the exchange - is still unanswered. So treat
what this returns as a cache that may have to be dropped, and keep derived
features in our own tables.
"""

from __future__ import annotations

import html
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import yaml

from trading_desk.domain.enums import SourceTier
from trading_desk.domain.news import FeedFailure, NewsArticle
from trading_desk.ports.clock import Clock

DEFAULT_SOURCES_PATH = Path("config/news_sources.yaml")
DEFAULT_TIMEOUT_SECONDS = 30.0

# Their validation limits. Exceeding either is an HTTP 422, so they are hard caps
# and not tuning knobs.
MAX_SYMBOLS_PER_CALL = 20
MAX_ROWS_PER_PAGE = 50

_HTML_TAG = re.compile(r"<[^>]+>")
_BLANK_RUN = re.compile(r"\n{3,}")


@dataclass(frozen=True)
class DrishtiPolicy:
    """Everything config says about reading Drishti."""

    base_url: str
    min_interval_seconds: float
    max_pages_per_batch: int
    default_publisher_tier: SourceTier
    publisher_tiers: dict[str, SourceTier]

    def tier_for(self, publisher: str) -> SourceTier:
        return self.publisher_tiers.get(publisher, self.default_publisher_tier)


def load_drishti_policy(path: Path = DEFAULT_SOURCES_PATH) -> DrishtiPolicy:
    if not path.is_file():
        raise FileNotFoundError(f"News sources not found at {path}")

    raw = yaml.safe_load(path.read_text()) or {}
    section = raw.get("drishti")
    if not section:
        raise ValueError(f"{path} has no 'drishti' section")

    tiers = {
        str(name): SourceTier(tier) for name, tier in (section.get("publisher_tiers") or {}).items()
    }
    return DrishtiPolicy(
        base_url=str(section["base_url"]).rstrip("/"),
        min_interval_seconds=float(section.get("min_interval_seconds", 6.5)),
        max_pages_per_batch=int(section.get("max_pages_per_batch", 6)),
        default_publisher_tier=SourceTier(section.get("default_publisher_tier", "TIER_2")),
        publisher_tiers=tiers,
    )


def _plain_text(markup: str) -> str:
    """Their `long_summary` is HTML for news and markdown for filings.

    The classifier and the credibility scorer read wording, so tags are noise
    that would also inflate every stored body. Headings and list items become
    their own lines rather than running together, because "IPO size cutPRISM
    plans" is not a sentence anyone can score.
    """
    with_breaks = re.sub(r"</(p|li|h\d|ol|ul|div)>", "\n", markup, flags=re.IGNORECASE)
    stripped = _HTML_TAG.sub("", with_breaks)
    return _BLANK_RUN.sub("\n\n", html.unescape(stripped)).strip()


def _symbols_of(row: dict[str, Any], wanted: frozenset[str]) -> tuple[str, ...]:
    """Which of the symbols we asked about this row is tagged with.

    Two shapes, both real, and getting the second wrong is silent:

    - One company: `"RELIANCE"`.
    - Several: **colon**-separated, `"RELIANCE:ONGC"` or
      `"HDFCBANK:ICICIBANK:AXISBANK:KOTAKBANK"`. Their request parameter takes
      commas, so commas are the natural guess and are accepted here too - but
      37% of rows measured on 2026-10-20 used colons, and splitting on commas
      alone left every one of those rows with no instrument at all. A sector
      story naming eight banks is exactly the kind a catalyst gate wants.

    Market-wide items come back as the string `"N/A"`, which must not become a
    symbol named "N/A" - that would link every macro story to a fake instrument.
    """
    raw = str(row.get("symbol") or "")
    parts = [part.strip() for part in re.split(r"[:,]", raw)]
    found = [part for part in parts if part not in ("", "N/A", "None")]
    # dict.fromkeys rather than set(): order follows their tagging, and a symbol
    # repeated in the string must not appear twice on the article.
    return tuple(symbol for symbol in dict.fromkeys(found) if symbol in wanted)


def _batched(symbols: list[str], size: int) -> list[list[str]]:
    return [symbols[start : start + size] for start in range(0, len(symbols), size)]


class DrishtiNewsAdapter:
    """Implements NewsPort against Drishti's `/news` endpoint."""

    def __init__(
        self,
        api_key: str,
        policy: DrishtiPolicy,
        clock: Clock,
        http_client: httpx.Client | None = None,
        sleep: Any = time.sleep,
    ) -> None:
        self._policy = policy
        self._clock = clock
        self._sleep = sleep
        # Sent per request rather than set on the client, so the key still goes
        # out when a caller injects its own client. Setting it on the client we
        # happen to build is a guard that disappears exactly when someone
        # substitutes the transport.
        self._auth_headers = {"X-API-Key": api_key, "Accept": "application/json"}
        self._http = http_client or httpx.Client(
            timeout=DEFAULT_TIMEOUT_SECONDS,
            follow_redirects=True,
        )
        self._last_request_at: float | None = None
        self._failures: list[FeedFailure] = []

    def fetch_since(self, since: datetime, symbols: list[str]) -> list[NewsArticle]:
        """Articles published after `since` tagged with any of `symbols`.

        Returns oldest-first. An empty `symbols` returns nothing rather than the
        whole market: this endpoint is billed, and a caller that forgot its
        universe should not silently buy every listed company's news.
        """
        if since.tzinfo is None:
            raise ValueError("since must be tz-aware")

        self._failures = []
        if not symbols:
            return []

        wanted = frozenset(symbols)
        by_id: dict[str, NewsArticle] = {}
        for batch in _batched(sorted(symbols), MAX_SYMBOLS_PER_CALL):
            for row in self._rows_for(batch, since):
                article = self._as_article(row, wanted)
                if article is not None:
                    # Two batches can return the same market-wide row. Keyed by
                    # their id so it is stored once.
                    by_id[article.article_id] = article

        return sorted(by_id.values(), key=lambda article: article.published_at)

    def recent_failures(self) -> list[FeedFailure]:
        """Batches that could not be read on the last `fetch_since`.

        A 429 and a quiet news day both return zero articles; only this tells
        them apart (CLAUDE.md rule 6).
        """
        return list(self._failures)

    def _rows_for(self, batch: list[str], since: datetime) -> list[dict[str, Any]]:
        """Every page for one batch of symbols, stopping at the configured cap."""
        rows: list[dict[str, Any]] = []
        for page in range(1, self._policy.max_pages_per_batch + 1):
            payload = self._get_page(batch, since, page)
            if payload is None:
                break
            rows.extend(payload.get("data") or [])
            if not payload.get("has_next"):
                break
        return rows

    def _get_page(self, batch: list[str], since: datetime, page: int) -> dict[str, Any] | None:
        """One request. A failure is recorded and ends this batch, never fatal."""
        self._wait_for_politeness()
        params: dict[str, str | int] = {
            "symbols": ",".join(batch),
            "from": since.date().isoformat(),
            "page": page,
            "limit": MAX_ROWS_PER_PAGE,
        }
        label = f"drishti/news p{page} [{batch[0]}..{batch[-1]}]"

        try:
            response = self._http.get(
                f"{self._policy.base_url}/news", params=params, headers=self._auth_headers
            )
        except httpx.HTTPError as error:
            self._failures.append(FeedFailure(label, type(error).__name__))
            return None

        if response.status_code != httpx.codes.OK:
            self._failures.append(FeedFailure(label, f"HTTP {response.status_code}"))
            return None

        try:
            payload = response.json()
        except ValueError:
            self._failures.append(FeedFailure(label, "response was not JSON"))
            return None

        if not isinstance(payload, dict):
            self._failures.append(FeedFailure(label, "response was not an object"))
            return None
        return payload

    def _as_article(self, row: dict[str, Any], wanted: frozenset[str]) -> NewsArticle | None:
        """One row, or None if it is unusable.

        A row is dropped rather than patched when it has no id, no headline or an
        unparseable date. Inventing a timestamp for a news item would put it in
        the wrong session, which is the kind of guess rule 6 forbids.
        """
        article_id = str(row.get("id") or "")
        headline = str(row.get("specific_title") or row.get("title") or "")
        if not article_id or not headline:
            return None

        published_at = _parse_published(row.get("date"))
        if published_at is None:
            return None

        body = _plain_text(str(row.get("long_summary") or row.get("summary") or ""))
        publisher = str(row.get("source") or "unknown")
        return NewsArticle(
            article_id=f"drishti:{article_id}",
            headline=headline,
            body=body,
            url=str(row.get("link") or ""),
            publisher=publisher,
            source_tier=self._policy.tier_for(publisher),
            published_at=published_at,
            ingested_at=self._clock.utc_now(),
            instrument_symbols=_symbols_of(row, wanted),
        )

    def _wait_for_politeness(self) -> None:
        """The sandbox plan is 10 requests a minute; exceeding it returns 429."""
        if self._last_request_at is not None:
            elapsed = time.monotonic() - self._last_request_at
            remaining = self._policy.min_interval_seconds - elapsed
            if remaining > 0:
                self._sleep(remaining)
        self._last_request_at = time.monotonic()

    def close(self) -> None:
        self._http.close()


def _parse_published(value: Any) -> datetime | None:
    """Their dates are ISO-8601 with an offset, e.g. 2026-10-20T11:06:17+00:00."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None
