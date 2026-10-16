"""Weekends and configured holidays are closed; everything else is open."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from trading_desk.application.calendars import load_trading_calendar

CONFIG = Path("config/holidays.yaml")


def test_a_normal_weekday_is_a_trading_day() -> None:
    assert load_trading_calendar(CONFIG).is_trading_day(date(2026, 10, 16)) is True


def test_weekends_are_closed() -> None:
    calendar = load_trading_calendar(CONFIG)

    assert calendar.is_trading_day(date(2026, 10, 17)) is False  # Saturday
    assert calendar.is_trading_day(date(2026, 10, 18)) is False  # Sunday


def test_dussehra_is_closed() -> None:
    """2026-10-20 is why the NIFTY weekly expiry moved to Monday the 19th (ISS-004)."""
    assert load_trading_calendar(CONFIG).is_trading_day(date(2026, 10, 20)) is False


def test_the_reason_is_reported_so_a_skip_is_explainable() -> None:
    calendar = load_trading_calendar(CONFIG)

    assert calendar.reason_closed(date(2026, 10, 17)) == "Saturday"
    assert calendar.reason_closed(date(2026, 10, 20)) == "exchange holiday"
    assert calendar.reason_closed(date(2026, 10, 16)) is None


def test_muhurat_is_listed_as_a_special_session_not_a_trading_day() -> None:
    """Capturing a short evening session would put an outlier in the IV series."""
    calendar = load_trading_calendar(CONFIG)

    assert date(2026, 11, 8) in calendar.special_sessions
    assert calendar.is_trading_day(date(2026, 11, 8)) is False  # it is a Sunday


def test_the_calendar_declares_whether_it_has_been_verified() -> None:
    """Unverified means the app warns at startup rather than quietly trusting it."""
    assert load_trading_calendar(CONFIG).verified is False


def test_a_missing_calendar_fails_loudly() -> None:
    with pytest.raises(FileNotFoundError, match="Holiday calendar"):
        load_trading_calendar(Path("config/absent.yaml"))
