"""The real clock. The only place in the codebase allowed to read the system time.

`IST` and `to_ist` live in `domain/timezones.py` and are re-exported here, because
every existing caller imports them from this module and a timezone is not an
adapter concern. See that module for why they moved.
"""

from __future__ import annotations

from datetime import UTC, datetime

from trading_desk.domain.timezones import IST, to_ist

__all__ = ["IST", "SystemClock", "to_ist"]


class SystemClock:
    """Reads the operating system clock."""

    def utc_now(self) -> datetime:
        return datetime.now(UTC)
