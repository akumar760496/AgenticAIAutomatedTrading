"""The real clock is tz-aware UTC; the fake does not move on its own."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from tests.fakes.clock import FrozenClock
from trading_desk.adapters.clock import IST, SystemClock, to_ist


def test_system_clock_returns_tz_aware_utc() -> None:
    now = SystemClock().utc_now()

    assert now.tzinfo is not None
    assert now.utcoffset() == timedelta(0)


def test_to_ist_converts_correctly() -> None:
    """IST is UTC+5:30. 04:00 UTC is 09:30 IST - the market open."""
    utc_moment = datetime(2026, 10, 15, 4, 0, tzinfo=UTC)

    ist_moment = to_ist(utc_moment)

    assert ist_moment.hour == 9
    assert ist_moment.minute == 30
    assert ist_moment.tzinfo == IST


def test_to_ist_rejects_a_naive_datetime() -> None:
    with pytest.raises(ValueError, match="naive"):
        to_ist(datetime(2026, 10, 15, 4, 0))  # noqa: DTZ001 - deliberately naive


def test_frozen_clock_does_not_move() -> None:
    clock = FrozenClock(datetime(2026, 10, 15, 4, 0, tzinfo=UTC))

    first = clock.utc_now()
    second = clock.utc_now()

    assert first == second


def test_frozen_clock_advances_only_when_told() -> None:
    clock = FrozenClock(datetime(2026, 10, 15, 4, 0, tzinfo=UTC))

    clock.advance(timedelta(hours=2))

    assert clock.utc_now().hour == 6


def test_frozen_clock_rejects_a_naive_datetime() -> None:
    with pytest.raises(ValueError, match="naive"):
        FrozenClock(datetime(2026, 10, 15, 4, 0))  # noqa: DTZ001 - deliberately naive
