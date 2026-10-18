"""Reading the exchange's expiry list backwards to find holidays.

The fixtures are the real lists Dhan returned on 2026-10-18, because the whole
point of ISS-004 is that invented ones agree with the weekday rule and real ones
do not.
"""

from __future__ import annotations

from datetime import date

from trading_desk.domain.expiries import (
    DisplacedExpiry,
    displaced_expiries,
    weekly_expiries,
)

# Live NIFTY list, 2026-10-18. Three Tuesdays are missing: 10-20 Dussehra,
# 11-10 Balipratipada, 11-24 Guru Nanak Jayanti.
NIFTY_LIVE = [
    date(2026, 10, 19),  # Mon
    date(2026, 10, 27),  # Tue
    date(2026, 11, 3),  # Tue
    date(2026, 11, 9),  # Mon
    date(2026, 11, 17),  # Tue
    date(2026, 11, 23),  # Mon
    date(2026, 12, 29),  # Tue, monthly
    date(2027, 3, 30),  # Tue, quarterly
    date(2027, 6, 29),
    date(2027, 9, 28),
]

# BANKNIFTY is monthly only, so there is no weekly run to reason about.
BANKNIFTY_LIVE = [
    date(2026, 10, 27),
    date(2026, 11, 23),
    date(2026, 12, 29),
    date(2027, 3, 30),
]


class TestWeeklyRun:
    def test_stops_at_the_jump_to_monthly_contracts(self) -> None:
        """Treating the sparse tail as weekly would invent months of holidays."""
        assert weekly_expiries(NIFTY_LIVE) == NIFTY_LIVE[:6]

    def test_an_eight_day_gap_is_still_weekly(self) -> None:
        """A week containing a holiday produces an eight or nine day gap."""
        run = weekly_expiries([date(2026, 10, 19), date(2026, 10, 27)])

        assert len(run) == 2

    def test_an_empty_list_has_no_run(self) -> None:
        assert weekly_expiries([]) == []

    def test_input_order_does_not_matter(self) -> None:
        assert weekly_expiries(list(reversed(NIFTY_LIVE))) == NIFTY_LIVE[:6]


class TestDisplacedExpiries:
    def test_finds_all_three_holidays_in_the_live_list(self) -> None:
        found = displaced_expiries(NIFTY_LIVE)

        assert [item.expected for item in found] == [
            date(2026, 10, 20),
            date(2026, 11, 10),
            date(2026, 11, 24),
        ]

    def test_finds_the_displacement_at_the_end_of_the_run(self) -> None:
        """2026-11-24 has no listed expiry after it. An earlier implementation
        scanned for Tuesdays missing *between* two listed expiries and silently
        missed exactly this one - the case the check existed for."""
        found = displaced_expiries(NIFTY_LIVE)

        assert DisplacedExpiry(expected=date(2026, 11, 24), actual=date(2026, 11, 23)) in found

    def test_reports_how_far_the_expiry_moved(self) -> None:
        found = displaced_expiries(NIFTY_LIVE)

        assert all(item.moved_earlier_by_days == 1 for item in found)

    def test_an_all_tuesday_list_has_no_displacements(self) -> None:
        clean = [date(2026, 10, 27), date(2026, 11, 3), date(2026, 11, 17)]

        assert displaced_expiries(clean) == []

    def test_a_sparse_monthly_list_yields_nothing(self) -> None:
        """BANKNIFTY's 11-23 is a Monday, but with no weekly run around it there
        is no weekday pattern to call it a displacement from."""
        assert displaced_expiries(BANKNIFTY_LIVE) == []

    def test_a_single_expiry_proves_nothing(self) -> None:
        assert displaced_expiries([date(2026, 11, 23)]) == []

    def test_a_long_weekend_moving_expiry_to_friday_is_found(self) -> None:
        """Monday and Tuesday both shut pushes the expiry back four days."""
        found = displaced_expiries([date(2026, 10, 27), date(2026, 10, 30), date(2026, 11, 10)])

        assert found[0].expected == date(2026, 11, 3)
        assert found[0].actual == date(2026, 10, 30)
        assert found[0].moved_earlier_by_days == 4
