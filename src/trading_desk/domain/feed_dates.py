"""Parsing the timestamp an RSS feed puts on an item.

Three feeds, three shapes, and getting this wrong is silent. The adapter skips
an item it cannot date, so a format it does not understand does not raise - it
produces an empty feed that looks exactly like a quiet news day. Both formats
below were wrong on the first attempt against live data:

- The standard library's `parsedate_to_datetime` raises `ValueError` on SEBI's
  "16 Oct, 2026 +0530" (comma after the month, no time, no weekday). All 30
  items were dropped.
- A feed that omits the offset parses to a *naive* datetime, which is rejected
  rather than guessed at: assuming the wrong zone misdates every item by 5h30m.

So the format is declared per feed in `config/news_sources.yaml` and verified
against the live feed, rather than sniffed at runtime. A feed that changes its
format starts failing loudly instead of going quietly empty.
"""

from __future__ import annotations

from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from enum import StrEnum

from trading_desk.domain.timezones import IST


class FeedDateFormat(StrEnum):
    """The shape of a feed's `pubDate`, declared in config per feed."""

    # "Sun, 18 Oct 2026 20:58:04 +0530" - RFC 822 with an offset, the norm.
    RFC822 = "rfc822"

    # "16 Oct, 2026 +0530" - a date with an offset but no time (SEBI).
    DATE_ONLY = "date_only"

    @property
    def is_date_only(self) -> bool:
        """Whether items carry a date but no time of day.

        The caller needs this: an item stamped midnight cannot be filtered by
        instant without losing everything the feed adds later that same day.
        """
        return self is FeedDateFormat.DATE_ONLY


def parse_published(raw: str, date_format: FeedDateFormat) -> datetime | None:
    """The item's publication time in UTC, or None if it cannot be read.

    None means "unusable", never "now". A guessed timestamp would place an item
    on the wrong side of every freshness and lookback comparison downstream.
    """
    text = raw.strip()
    if not text:
        return None

    if date_format is FeedDateFormat.DATE_ONLY:
        parsed = _parse_date_only(text)
    else:
        parsed = _parse_rfc822(text)

    if parsed is None:
        return None
    return parsed.astimezone(UTC)


def _parse_rfc822(text: str) -> datetime | None:
    try:
        parsed = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    # No offset means no basis for choosing a timezone, so the item is dropped.
    return parsed if parsed.tzinfo is not None else None


def _parse_date_only(text: str) -> datetime | None:
    """A date with no time, placed at midnight in the feed's own offset.

    Midnight, not noon and not the fetch time: it is the only choice that keeps
    the item on the calendar day the publisher assigned it.
    """
    try:
        # SEBI: "16 Oct, 2026 +0530" - comma after the month, offset, no time.
        parsed = datetime.strptime(text, "%d %b, %Y %z")
    except ValueError:
        return None
    # strptime applies %z, so this is already aware. The fallback covers a feed
    # that drops the offset from one item while keeping it on the rest.
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=IST)
