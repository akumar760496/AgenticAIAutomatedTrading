"""NSE corporate announcements, mapped into domain Filings.

The public endpoint behind the exchange's "Corporate Filings - Announcements"
page. Free, no key, and authoritative: these are the Regulation 30 disclosures
companies are legally required to make, which is why they are SourceTier.EXCHANGE
rather than reporting.

Three things this adapter has to get right:

1. **It refuses a plain request.** A bare GET returns nothing at all - not a 403,
   no response. It needs a browser User-Agent and a Referer naming the page the
   call would have come from, and a session cookie obtained by visiting that page
   first. Verified 2026-10-18: without headers, no response; with them, HTTP 200.
2. **`an_dt` is naive IST**, formatted "18-Oct-2026 17:09:16". The timezone is
   attached here, at the edge, so nothing inland sees a naive datetime
   (CLAUDE.md rule 5). Treating it as UTC would date every announcement 5h30m
   early, moving evening filings into the previous day.
3. **The body is `attchmntText`, and it is untrusted.** It is free text a company
   submitted. It is stored verbatim and inspected, never filtered.
4. **The date range is mandatory, not an optimisation.** Called without
   `from_date`/`to_date` the endpoint answers with the twenty most recent
   announcements and gives no sign that it has truncated. Measured 2026-10-18:
   20 records bare, 1,368 for the same three-day window with the range supplied.
   During market hours twenty announcements can be minutes, so a poller without
   the range would quietly miss filings and look healthy doing it.

Politeness: this is someone else's public website, not a paid API. One request
per poll, a real User-Agent, and a conservative interval.
"""

from __future__ import annotations

import contextlib
from datetime import datetime
from typing import Any

import httpx

from trading_desk.domain.news import Filing
from trading_desk.domain.timezones import IST, to_ist
from trading_desk.ports.clock import Clock

BASE_URL = "https://www.nseindia.com"
ANNOUNCEMENTS_PATH = "/api/corporate-announcements"

# The page a browser would have been on. Without it the API does not answer.
REFERER = f"{BASE_URL}/companies-listing/corporate-filings-announcements"

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": REFERER,
}

# "18-Oct-2026 17:09:16", in IST with no offset.
ANNOUNCEMENT_TIME_FORMAT = "%d-%b-%Y %H:%M:%S"

# The query parameters want a different shape again: "18-10-2026".
QUERY_DATE_FORMAT = "%d-%m-%Y"

DEFAULT_TIMEOUT_SECONDS = 20.0


class NseAnnouncementError(RuntimeError):
    """A request to NSE failed. Carries the status, never a cookie."""


class NseAnnouncementsAdapter:
    """Implements FilingsPort against NSE's public announcements endpoint."""

    def __init__(
        self,
        clock: Clock,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._clock = clock
        self._http = http_client or httpx.Client(
            base_url=BASE_URL,
            timeout=DEFAULT_TIMEOUT_SECONDS,
            headers=BROWSER_HEADERS,
            follow_redirects=True,
        )
        self._warmed_up = False

    def _warm_up(self) -> None:
        """Visit the page once so the client holds whatever cookie it sets.

        Failures here are swallowed: on some runs the API answers without a prior
        visit, and turning a warm-up hiccup into a hard failure would lose a poll
        that might have worked. A genuinely unauthorised call fails loudly below.
        """
        if self._warmed_up:
            return
        with contextlib.suppress(httpx.HTTPError):
            self._http.get(REFERER)
        self._warmed_up = True

    def fetch_since(self, since: datetime, symbols: list[str]) -> list[Filing]:
        """Announcements filed after `since` for any of `symbols`.

        The date range is passed to the endpoint; symbol filtering happens here,
        because one request covering the window is politer to someone else's
        public website than 26 requests for data a single call returns.

        `since` must be tz-aware: a naive value would compare wrongly against IST
        timestamps by 5h30m.
        """
        if since.tzinfo is None:
            raise ValueError("since must be tz-aware")

        self._warm_up()
        wanted = {symbol.upper() for symbol in symbols}
        received_at = self._clock.utc_now()

        params = {
            "index": "equities",
            "from_date": to_ist(since).strftime(QUERY_DATE_FORMAT),
            "to_date": to_ist(received_at).strftime(QUERY_DATE_FORMAT),
        }

        try:
            response = self._http.get(ANNOUNCEMENTS_PATH, params=params)
        except httpx.HTTPError as error:
            raise NseAnnouncementError(
                f"NSE announcements request failed ({type(error).__name__})"
            ) from error

        if response.status_code != httpx.codes.OK:
            raise NseAnnouncementError(
                f"NSE announcements: HTTP {response.status_code} - {response.text[:120]}"
            )

        records: list[dict[str, Any]] = response.json()

        filings = []
        for record in records:
            filing = _map_filing(record, received_at)
            if filing is None:
                continue
            if wanted and filing.symbol not in wanted:
                continue
            if filing.filed_at <= since:
                continue
            filings.append(filing)

        return sorted(filings, key=lambda item: item.filed_at)

    def close(self) -> None:
        self._http.close()


def _map_filing(record: dict[str, Any], received_at: datetime) -> Filing | None:
    """One announcement, or None when it is unusable.

    A record with no symbol or no timestamp is skipped rather than guessed at:
    an announcement attached to the wrong instrument is worse than a missing one.
    """
    symbol = (record.get("symbol") or "").strip().upper()
    raw_time = (record.get("an_dt") or "").strip()
    if not symbol or not raw_time:
        return None

    filed_at = _parse_ist(raw_time)
    if filed_at is None:
        return None

    attachment = (record.get("attchmntFile") or "").strip()
    return Filing(
        # seq_id is NSE's own identifier; the timestamp fallback keeps the id
        # stable for a record that lacks one rather than inventing a random id
        # that would re-ingest as new on every poll.
        filing_id=str(record.get("seq_id") or f"{symbol}-{raw_time}"),
        symbol=symbol,
        subject=(record.get("desc") or "").strip(),
        body=(record.get("attchmntText") or "").strip(),
        url=attachment or REFERER,
        filed_at=filed_at,
        ingested_at=received_at,
        attachments=(attachment,) if attachment else (),
    )


def _parse_ist(raw: str) -> datetime | None:
    """Parse NSE's naive IST timestamp and attach the timezone."""
    try:
        naive = datetime.strptime(raw, ANNOUNCEMENT_TIME_FORMAT)  # noqa: DTZ007
    except ValueError:
        return None
    return naive.replace(tzinfo=IST)
