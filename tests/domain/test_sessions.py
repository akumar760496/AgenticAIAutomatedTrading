"""Session boundaries and bar alignment.

Bars align to the session open, not the wall clock. Getting this wrong shifts
every boundary by two minutes and still reconciles perfectly against itself.
"""

from __future__ import annotations

from datetime import date, datetime, time

import pytest

from trading_desk.domain.sessions import SessionHours, bar_start, session_for
from trading_desk.domain.timezones import IST

HOURS = SessionHours(open_ist=time(9, 15), close_ist=time(15, 30))
DAY = date(2026, 10, 19)  # a Monday
SESSION = session_for(DAY, HOURS)


def _ist(hour: int, minute: int, second: int = 0) -> datetime:
    return datetime(2026, 10, 19, hour, minute, second, tzinfo=IST)


class TestSessionHours:
    def test_rejects_a_close_before_the_open(self) -> None:
        with pytest.raises(ValueError, match="after its close"):
            SessionHours(open_ist=time(15, 30), close_ist=time(9, 15))


class TestSession:
    def test_spans_three_hundred_and_seventy_five_minutes(self) -> None:
        """09:15 to 15:30 is 6h15m. A wrong tz would make it 6h or 11h45m."""
        assert SESSION.minutes == 375

    def test_the_opening_instant_is_inside(self) -> None:
        assert SESSION.contains(_ist(9, 15, 0))

    def test_one_second_before_the_open_is_outside(self) -> None:
        assert not SESSION.contains(_ist(9, 14, 59))

    def test_the_last_second_is_inside(self) -> None:
        assert SESSION.contains(_ist(15, 29, 59))

    def test_the_closing_instant_is_outside(self) -> None:
        """Half-open: a print at 15:30:00 is the closing auction, not the last bar."""
        assert not SESSION.contains(_ist(15, 30, 0))

    def test_a_naive_datetime_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="naive"):
            SESSION.contains(datetime(2026, 10, 19, 10, 0))  # noqa: DTZ001


class TestBarStart:
    def test_a_tick_at_the_open_starts_the_first_bar(self) -> None:
        assert bar_start(_ist(9, 15, 0), 5, SESSION) == _ist(9, 15)

    def test_bars_align_to_the_open_not_the_hour(self) -> None:
        """09:17 belongs to the 09:15 bar, not an 09:15-shaped stub off 09:00."""
        assert bar_start(_ist(9, 17, 30), 5, SESSION) == _ist(9, 15)
        assert bar_start(_ist(9, 20, 0), 5, SESSION) == _ist(9, 20)
        assert bar_start(_ist(10, 3, 0), 5, SESSION) == _ist(10, 0)

    def test_the_last_second_falls_in_the_final_bar(self) -> None:
        assert bar_start(_ist(15, 29, 59), 5, SESSION) == _ist(15, 25)

    def test_a_moment_outside_the_session_has_no_bar(self) -> None:
        with pytest.raises(ValueError, match="outside the session"):
            bar_start(_ist(15, 30, 0), 5, SESSION)

    def test_a_non_dividing_interval_still_aligns_from_the_open(self) -> None:
        """375 minutes is not divisible by 7; the final bar is simply short."""
        assert bar_start(_ist(9, 15), 7, SESSION) == _ist(9, 15)
        assert bar_start(_ist(9, 22), 7, SESSION) == _ist(9, 22)
        assert bar_start(_ist(9, 21, 59), 7, SESSION) == _ist(9, 15)

    def test_a_zero_interval_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="interval must be positive"):
            bar_start(_ist(10, 0), 0, SESSION)
