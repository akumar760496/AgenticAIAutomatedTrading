"""Reasoning about expiry dates that the exchange has already published.

**Nothing here computes an expiry.** ISS-004: the plan said "NIFTY weekly on
Tuesday" and the exchange's own list reads Mon 2026-10-19, Tue 10-27, Tue 11-03,
Mon 11-09, Tue 11-17, Mon 11-23. Three Tuesdays are missing because they are
holidays, and the expiry moved to the Monday before. Any code that derived a date
from a weekday rule would name contracts that do not exist.

What this module does instead is read the published list *backwards*: a Tuesday
that the exchange skipped, in a stretch where it otherwise lists every Tuesday,
is a Tuesday the market was shut. That turns the expiry list into evidence about
the holiday calendar - primary evidence, from the venue, rather than a calendar
website. `verify 1_6` uses it to catch a holiday file that has gone stale, which
is how 2026-11-24 was found missing.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from itertools import pairwise

TUESDAY = 1  # date.weekday(): Monday is 0

# One expiry proves nothing about a weekday pattern; a displacement needs a run.
MIN_WEEKLY_RUN = 2


@dataclass(frozen=True)
class DisplacedExpiry:
    """A weekly expiry that did not fall on its usual weekday."""

    expected: date  # the Tuesday it would have been
    actual: date  # where the exchange actually put it

    @property
    def moved_earlier_by_days(self) -> int:
        return (self.expected - self.actual).days


def weekly_expiries(expiries: Sequence[date], max_gap_days: int = 10) -> list[date]:
    """The dense, weekly part of an expiry list.

    An expiry list runs weekly for a few weeks and then jumps to monthly and
    quarterly contracts - NIFTY on 2026-10-18 lists six weeklies to 11-23, then
    12-29, then 2027-03-30. Treating that sparse tail as weekly would read every
    skipped Tuesday in it as a holiday and invent months of closures.

    So the weekly run ends at the first gap wider than `max_gap_days`. Ten days
    rather than seven, because a week containing a holiday already produces an
    eight or nine day gap.
    """
    ordered = sorted(expiries)
    if not ordered:
        return []

    weekly = [ordered[0]]
    for previous, current in pairwise(ordered):
        if (current - previous).days > max_gap_days:
            break
        weekly.append(current)
    return weekly


def displaced_expiries(expiries: Sequence[date]) -> list[DisplacedExpiry]:
    """Weekly expiries that did not land on their usual Tuesday.

    The rule is the direct one: in the weekly run, **an expiry that is not a
    Tuesday is itself the evidence**, and the Tuesday it was moved off is the next
    Tuesday after it.

    The first attempt instead scanned for Tuesdays missing from the list, which
    looked equivalent and was not: it could only see a displacement with a listed
    expiry on both sides, so it silently missed 2026-11-24 - the one whose Monday
    sits at the end of the published run, and the one this check existed to find.
    """
    weekly = weekly_expiries(expiries)
    if len(weekly) < MIN_WEEKLY_RUN:
        return []

    displaced = []
    for expiry in weekly:
        if expiry.weekday() == TUESDAY:
            continue
        tuesday = _next_tuesday_after(expiry)
        if tuesday is not None:
            displaced.append(DisplacedExpiry(expected=tuesday, actual=expiry))
    return displaced


def _next_tuesday_after(day: date) -> date | None:
    """The first Tuesday following `day`, within a week.

    A holiday shift moves an expiry earlier - one day for a single Tuesday
    closure, more across a long weekend - so the Tuesday it belongs to is always
    the next one.
    """
    offset = (TUESDAY - day.weekday()) % 7
    if offset == 0:
        return None
    return day + timedelta(days=offset)
