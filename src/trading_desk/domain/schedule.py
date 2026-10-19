"""Counting unattended sessions from a run log.

`docs/12` step 3.7 asks for "five consecutive unattended sessions on schedule.
Verify from logs, not memory." Memory is the part worth taking seriously: the
natural way to answer this is to remember that it seemed to work, and the
natural failure is a job that quietly stopped firing on the 23rd while the
laptop was asleep.

So the question is answered by arithmetic over recorded run dates, and it is
answered with **two numbers, not one**:

- how long the streak is, and
- how many trading sessions have passed since the last run in it.

One number alone lies in both directions. A streak of five that ended three
weeks ago is not a working scheduler, and a job that ran this morning after
nothing for a month is not five sessions either.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from datetime import date, timedelta

from trading_desk.domain.sessions import is_trading_day, sessions_between

# A streak is only ever counted back this far. The question is "is it running
# now", and a year of history does not make the answer better.
MAX_STREAK_LOOKBACK_DAYS = 120


@dataclass(frozen=True)
class SessionStreak:
    """Consecutive trading sessions a job ran, and how stale that run is."""

    length: int
    latest: date | None
    sessions_since_latest: int

    def describe(self, needed: int) -> str:
        if self.latest is None:
            return f"0 of {needed} consecutive sessions (the job has never run)"
        return (
            f"{self.length} of {needed} consecutive sessions, "
            f"latest {self.latest:%Y-%m-%d} "
            f"({self.sessions_since_latest} session(s) ago)"
        )


def consecutive_sessions_run(
    *,
    ran_on: Collection[date],
    up_to: date,
    holidays: frozenset[date],
) -> SessionStreak:
    """The streak of trading sessions ending at the job's most recent run.

    Measured from the latest run rather than from `up_to`, because a check made
    at 07:00 would otherwise score a perfectly healthy scheduler at zero for
    not having run yet today. Staleness is reported separately so that
    distinction is visible instead of smoothed over.
    """
    dates = {day for day in ran_on if day <= up_to}
    if not dates:
        return SessionStreak(length=0, latest=None, sessions_since_latest=0)

    latest = max(dates)

    length = 0
    day = latest
    earliest = latest - timedelta(days=MAX_STREAK_LOOKBACK_DAYS)
    while day >= earliest:
        if not is_trading_day(day, holidays):
            day -= timedelta(days=1)
            continue
        if day not in dates:
            break
        length += 1
        day -= timedelta(days=1)

    return SessionStreak(
        length=length,
        latest=latest,
        sessions_since_latest=sessions_between(latest, up_to, holidays),
    )
