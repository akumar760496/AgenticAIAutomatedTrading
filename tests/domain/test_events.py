"""Event windows: what is coming, for whom, between when and when."""

from __future__ import annotations

from datetime import date

import pytest

from trading_desk.domain.events import (
    EventKind,
    MarketEvent,
    events_between,
    within_days,
)

RBI = MarketEvent(
    kind=EventKind.RBI_POLICY,
    on_date=date(2026, 12, 4),
    description="RBI MPC decision",
    verified=True,
)
RESULTS = MarketEvent(
    kind=EventKind.RESULTS,
    on_date=date(2026, 11, 5),
    description="Q2 results",
    symbol="RELIANCE",
)
OTHER_RESULTS = MarketEvent(
    kind=EventKind.RESULTS,
    on_date=date(2026, 11, 6),
    description="Q2 results",
    symbol="TCS",
)
ALL = [RBI, RESULTS, OTHER_RESULTS]


class TestAffects:
    def test_a_market_wide_event_affects_every_instrument(self) -> None:
        assert RBI.affects("RELIANCE")
        assert RBI.affects("TCS")

    def test_a_company_event_affects_only_that_company(self) -> None:
        assert RESULTS.affects("RELIANCE")
        assert not RESULTS.affects("TCS")


class TestEventsBetween:
    def test_filters_by_date_range(self) -> None:
        found = events_between(ALL, date(2026, 11, 1), date(2026, 11, 30))

        assert found == [RESULTS, OTHER_RESULTS]

    def test_both_ends_are_inclusive(self) -> None:
        """An event on the expiry date is the one that matters most; excluding
        it would silently drop the worst case."""
        assert events_between(ALL, date(2026, 12, 4), date(2026, 12, 4)) == [RBI]

    def test_filtering_by_symbol_keeps_market_wide_events(self) -> None:
        found = events_between(ALL, date(2026, 11, 1), date(2026, 12, 31), "RELIANCE")

        assert found == [RESULTS, RBI]
        assert OTHER_RESULTS not in found

    def test_results_are_sorted_by_date(self) -> None:
        found = events_between(list(reversed(ALL)), date(2026, 1, 1), date(2027, 1, 1))

        assert [event.on_date for event in found] == sorted(event.on_date for event in ALL)

    def test_a_backwards_range_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="is after end"):
            events_between(ALL, date(2026, 12, 1), date(2026, 11, 1))


class TestWithinDays:
    def test_counts_from_today_inclusive(self) -> None:
        assert within_days(ALL, date(2026, 11, 5), 0) == [RESULTS]

    def test_includes_the_final_day(self) -> None:
        assert within_days(ALL, date(2026, 11, 1), 4) == [RESULTS]

    def test_excludes_anything_past_the_window(self) -> None:
        assert within_days(ALL, date(2026, 11, 1), 3) == []

    def test_a_negative_window_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="must not be negative"):
            within_days(ALL, date(2026, 11, 1), -1)


class TestDaysUntil:
    def test_counts_forward(self) -> None:
        assert RBI.days_until(date(2026, 12, 1)) == 3

    def test_is_negative_once_past(self) -> None:
        assert RBI.days_until(date(2026, 12, 10)) == -6
