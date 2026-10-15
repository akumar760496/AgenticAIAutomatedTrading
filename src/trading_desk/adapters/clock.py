"""The real clock. The only place in the codebase allowed to call datetime.now().

IST conversion belongs at the edges - a briefing rendered for a human, a market
session boundary. Internally everything is UTC, so comparisons never depend on
which side of a DST change a server happens to be (India has none, but the servers
and the libraries do not know that).
"""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


class SystemClock:
    """Reads the operating system clock."""

    def utc_now(self) -> datetime:
        return datetime.now(UTC)


def to_ist(moment: datetime) -> datetime:
    """Convert a tz-aware instant to IST for display. Rejects naive datetimes."""
    if moment.tzinfo is None:
        raise ValueError("Cannot convert a naive datetime to IST - it has no meaning")
    return moment.astimezone(IST)
