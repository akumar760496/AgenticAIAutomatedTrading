"""The trading session, and which bar a moment falls into.

Pure arithmetic over times. Nothing here reads a clock: a session is built for a
date the caller supplies, which is what lets the same code serve a live feed, a
backfill and a test.

Bars are aligned to the **session open**, not to the wall clock. NSE opens at
09:15, so five-minute bars start 09:15, 09:20, ... 15:25. Aligning to the hour
instead would produce a two-minute stub at 09:15 and silently shift every bar
boundary, which is the kind of error that reconciles fine against itself and not
at all against the exchange.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from trading_desk.domain.timezones import IST


@dataclass(frozen=True)
class SessionHours:
    """Open and close in IST, as config states them."""

    open_ist: time
    close_ist: time

    def __post_init__(self) -> None:
        if self.open_ist >= self.close_ist:
            raise ValueError(f"session opens at {self.open_ist}, after its close {self.close_ist}")


@dataclass(frozen=True)
class TradingSession:
    """One day's session as two tz-aware instants.

    Half-open: `opens_at` is inside the session and `closes_at` is not. A trade
    stamped exactly at the close belongs to the closing auction, which this does
    not model, so it is outside rather than crammed into the last bar.
    """

    opens_at: datetime
    closes_at: datetime

    def contains(self, moment: datetime) -> bool:
        if moment.tzinfo is None:
            raise ValueError("Cannot place a naive datetime in a session")
        return self.opens_at <= moment < self.closes_at

    @property
    def minutes(self) -> int:
        return int((self.closes_at - self.opens_at).total_seconds() // 60)


def session_for(day: date, hours: SessionHours) -> TradingSession:
    """Build the session for one date. Takes the date; never reads a clock."""
    opens = datetime.combine(day, hours.open_ist, tzinfo=IST)
    closes = datetime.combine(day, hours.close_ist, tzinfo=IST)
    return TradingSession(opens_at=opens, closes_at=closes)


def bar_start(moment: datetime, interval_minutes: int, session: TradingSession) -> datetime:
    """The opening instant of the bar containing `moment`.

    Raises if the moment is outside the session, because there is no correct bar
    to put it in and picking one would invent data.
    """
    if interval_minutes <= 0:
        raise ValueError(f"interval must be positive, got {interval_minutes}")
    if not session.contains(moment):
        raise ValueError(f"{moment} is outside the session {session.opens_at}-{session.closes_at}")

    elapsed = int((moment - session.opens_at).total_seconds() // 60)
    whole_bars = elapsed // interval_minutes
    return session.opens_at + timedelta(minutes=whole_bars * interval_minutes)
