"""Feed timestamps. Every case here was a silent data loss against a live feed."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from trading_desk.domain.feed_dates import FeedDateFormat, parse_published


class TestRfc822:
    def test_an_offset_timestamp_converts_to_utc(self) -> None:
        parsed = parse_published("Sun, 18 Oct 2026 20:58:04 +0530", FeedDateFormat.RFC822)
        assert parsed == datetime(2026, 10, 18, 15, 28, 4, tzinfo=UTC)

    def test_a_timestamp_with_no_offset_is_refused(self) -> None:
        """RBI's feed looks like this. Guessing the zone misdates by 5h30m, and
        the item is dropped instead."""
        assert parse_published("Fri, 16 Oct 2026 18:30:00", FeedDateFormat.RFC822) is None


class TestDateOnly:
    def test_sebis_format_parses_to_midnight_ist(self) -> None:
        """ "16 Oct, 2026 +0530" - the stdlib RFC 822 parser raises on this, and
        all 30 SEBI items were being discarded before the format was declared."""
        parsed = parse_published("16 Oct, 2026 +0530", FeedDateFormat.DATE_ONLY)
        # Midnight on the 16th in IST is 18:30 UTC on the 15th.
        assert parsed == datetime(2026, 10, 15, 18, 30, tzinfo=UTC)

    def test_the_standard_parser_cannot_read_it(self) -> None:
        """The reason a per-feed format exists at all. If this ever starts
        passing, the extra format is dead code and should go."""
        assert parse_published("16 Oct, 2026 +0530", FeedDateFormat.RFC822) is None

    def test_only_the_date_only_format_reports_itself_as_such(self) -> None:
        assert FeedDateFormat.DATE_ONLY.is_date_only
        assert not FeedDateFormat.RFC822.is_date_only


@pytest.mark.parametrize("date_format", list(FeedDateFormat))
@pytest.mark.parametrize("raw", ["", "   ", "not a date", "2026-10-16T00:00:00Z"])
def test_unreadable_input_is_none_never_a_guess(raw: str, date_format: FeedDateFormat) -> None:
    """None means unusable. Substituting "now" would place the item inside
    every freshness window it should have failed."""
    assert parse_published(raw, date_format) is None
