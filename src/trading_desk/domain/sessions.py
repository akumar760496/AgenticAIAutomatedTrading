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


SATURDAY = 5
SUNDAY = 6


def is_trading_day(day: date, holidays: frozenset[date]) -> bool:
    """Weekday and not a listed holiday.

    Duplicates `application.calendars.TradingCalendar.is_trading_day` in
    behaviour but not in ownership: the calendar *file* and its provenance stay
    in the application layer, and this takes the resulting dates as an
    argument. Agents may not import `application`, and the catalyst-recency
    gate counts sessions.
    """
    if day.weekday() in (SATURDAY, SUNDAY):
        return False
    return day not in holidays


def sessions_between(earlier: date, later: date, holidays: frozenset[date]) -> int:
    """Trading sessions strictly after `earlier`, up to and including `later`.

    So a Friday evening filing read on Monday is **one** session old, not three
    days old - which is the distinction `config/setups.yaml` means by
    `max_sessions_since_catalyst: 2`.

    Returns 0 when `later` is not after `earlier`; a negative answer would mean
    a catalyst from the future, and the caller should see zero rather than a
    number that silently passes a recency check.
    """
    if later <= earlier:
        return 0
    count = 0
    day = earlier + timedelta(days=1)
    while day <= later:
        if is_trading_day(day, holidays):
            count += 1
        day += timedelta(days=1)
    return count
