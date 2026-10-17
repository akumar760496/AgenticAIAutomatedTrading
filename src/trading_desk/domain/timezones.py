"""The exchange's timezone, and conversion into it.

Pure: a timezone is reference data and a conversion is arithmetic. Neither reads a
clock, so both belong here rather than beside `SystemClock`.

They started out in `adapters/clock.py`, which was fine until the snapshotter
needed the IST trading date to check the calendar. Importing it from there would
have made an application module depend on an adapter - the dependency rule runs
the other way, and import-linter enforces it. The fix is this module rather than an
exception, because nothing about `to_ist` was ever adapter-shaped.

Internally everything stays UTC, so comparisons never depend on which side of a DST
change a server sits (India has none; the servers and libraries do not know that).
IST appears at the edges: a briefing for a human, a session boundary, an archive
partition keyed by trading date.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


def to_ist(moment: datetime) -> datetime:
    """Convert a tz-aware instant to IST. Rejects naive datetimes.

    A naive datetime is rejected rather than assumed to be UTC: the one time that
    assumption is wrong it is wrong by five and a half hours, which silently moves
    an event across a session boundary.
    """
    if moment.tzinfo is None:
        raise ValueError("Cannot convert a naive datetime to IST - it has no meaning")
    return moment.astimezone(IST)
