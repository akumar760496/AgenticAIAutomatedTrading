"""Counting consecutive unattended sessions from recorded run dates."""

from __future__ import annotations

from datetime import date, timedelta

from trading_desk.domain.schedule import consecutive_sessions_run

# 2026-10-19 is a Monday.
MONDAY = date(2026, 10, 19)
NO_HOLIDAYS: frozenset[date] = frozenset()


def _week_to(day: date) -> list[date]:
    """That day and the four weekdays before it."""
    days: list[date] = []
    cursor = day
    while len(days) < 5:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor -= timedelta(days=1)
    return days


class TestTheStreak:
    def test_five_weekdays_in_a_row_is_five_sessions(self) -> None:
        streak = consecutive_sessions_run(
            ran_on=_week_to(date(2026, 10, 16)), up_to=MONDAY, holidays=NO_HOLIDAYS
        )

        assert streak.length == 5

    def test_a_weekend_does_not_break_a_streak(self) -> None:
        """Friday then Monday is two consecutive sessions, not a gap of three."""
        streak = consecutive_sessions_run(
            ran_on=[date(2026, 10, 16), MONDAY], up_to=MONDAY, holidays=NO_HOLIDAYS
        )

        assert streak.length == 2

    def test_a_listed_holiday_does_not_break_a_streak_either(self) -> None:
        holiday = date(2026, 10, 15)  # a Thursday
        streak = consecutive_sessions_run(
            ran_on=[date(2026, 10, 14), date(2026, 10, 16)],
            up_to=MONDAY,
            holidays=frozenset({holiday}),
        )

        assert streak.length == 2

    def test_a_missed_trading_day_breaks_it(self) -> None:
        streak = consecutive_sessions_run(
            ran_on=[date(2026, 10, 14), date(2026, 10, 16)], up_to=MONDAY, holidays=NO_HOLIDAYS
        )

        assert streak.length == 1

    def test_never_run_is_zero_and_says_so(self) -> None:
        streak = consecutive_sessions_run(ran_on=[], up_to=MONDAY, holidays=NO_HOLIDAYS)

        assert streak.length == 0
        assert streak.latest is None
        assert "never run" in streak.describe(5)


class TestStaleness:
    def test_an_old_streak_is_reported_as_old(self) -> None:
        """Five in a row three weeks ago is not a working scheduler, and one
        number cannot say that."""
        streak = consecutive_sessions_run(
            ran_on=_week_to(date(2026, 9, 25)), up_to=MONDAY, holidays=NO_HOLIDAYS
        )

        assert streak.length == 5
        assert streak.sessions_since_latest > 5

    def test_a_check_made_before_todays_run_still_sees_the_streak(self) -> None:
        """Counting back from `up_to` would score a healthy scheduler at zero
        at 07:00, before the 08:15 job has fired."""
        streak = consecutive_sessions_run(
            ran_on=_week_to(date(2026, 10, 16)), up_to=MONDAY, holidays=NO_HOLIDAYS
        )

        assert streak.length == 5
        assert streak.sessions_since_latest == 1

    def test_a_run_dated_in_the_future_is_ignored(self) -> None:
        streak = consecutive_sessions_run(
            ran_on=[date(2026, 11, 30)], up_to=MONDAY, holidays=NO_HOLIDAYS
        )

        assert streak.length == 0
