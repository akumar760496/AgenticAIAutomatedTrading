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


def test_the_verified_flag_reflects_the_file(tmp_path: Path) -> None:
    """Unverified means the app warns at startup rather than quietly trusting it.

    This asserts the flag follows the file, not what the shipped file happens to
    say today. The previous version pinned `verified is False` and broke the day
    the calendar was actually verified - a test that recorded a fact rather than
    a property, which is the same trap three verify checks fell into.
    """
    unverified = tmp_path / "unverified.yaml"
    unverified.write_text(
        "version: 1\nverified: false\n2026:\n  - date: 2026-10-02\n    reason: X\n"
    )
    verified = tmp_path / "verified.yaml"
    verified.write_text("version: 1\nverified: true\n2026:\n  - date: 2026-10-02\n    reason: X\n")

    assert load_trading_calendar(unverified).verified is False
    assert load_trading_calendar(verified).verified is True


def test_every_shipped_holiday_states_its_evidence() -> None:
    """A date with no stated provenance is a date nobody can re-check."""
    calendar = load_trading_calendar(CONFIG)

    for day in calendar.holidays:
        assert calendar.evidence_for(day) in {"expiry_shift", "published"}


def test_guru_nanak_jayanti_is_listed() -> None:
    """Found by the expiry-shift audit, not a calendar: NIFTY and BANKNIFTY both
    moved their expiry to Monday 2026-11-23, which only happens if the Tuesday
    is shut. It was missing from version 1 of the file."""
    calendar = load_trading_calendar(CONFIG)

    assert calendar.is_trading_day(date(2026, 11, 24)) is False
    assert calendar.evidence_for(date(2026, 11, 24)) == "expiry_shift"


def test_missing_holidays_reports_a_tuesday_the_exchange_skipped() -> None:
    """The audit that keeps the file honest."""
    calendar = load_trading_calendar(CONFIG)

    known = date(2026, 11, 24)  # already listed
    unknown = date(2027, 3, 2)  # a Tuesday nothing knows about

    assert calendar.missing_holidays([known]) == []
    assert calendar.missing_holidays([known, unknown]) == [unknown]


def test_a_missing_calendar_fails_loudly() -> None:
    with pytest.raises(FileNotFoundError, match="Holiday calendar"):
        load_trading_calendar(Path("config/absent.yaml"))
