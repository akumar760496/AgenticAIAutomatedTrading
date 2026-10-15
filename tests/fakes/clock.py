"""A clock that does not move unless a test moves it."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta


class FrozenClock:
    """Returns a fixed instant. advance() moves it on by an explicit amount.

    Use this anywhere a test needs a time. A test that depends on the real clock
    fails once a year at a session boundary and nobody ever works out why.
    """

    def __init__(self, moment: datetime) -> None:
        if moment.tzinfo is None:
            raise ValueError("FrozenClock needs a tz-aware datetime, got a naive one")
        self._moment = moment.astimezone(UTC)

    def utc_now(self) -> datetime:
        return self._moment

    def advance(self, delta: timedelta) -> None:
        self._moment = self._moment + delta
