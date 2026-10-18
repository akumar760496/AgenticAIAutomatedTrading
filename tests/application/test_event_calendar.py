"""Loading the shipped event calendar, and what it promises."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from trading_desk.application.event_calendar import load_event_calendar
from trading_desk.domain.events import EventKind

CONFIG = Path("config/events.yaml")


def test_the_shipped_calendar_loads() -> None:
    assert len(load_event_calendar(CONFIG).events) > 0


def test_rbi_dates_are_traced_to_the_issuing_authority() -> None:
    """A verified date carries the url that proves it. Without that, 'verified'
    is just a flag somebody set."""
    policy = load_event_calendar(CONFIG).of_kind(EventKind.RBI_POLICY)

    assert policy
    for event in policy:
        assert event.verified
        assert "rbi.org.in" in event.source


def test_the_december_mpc_decision_is_listed() -> None:
    """Meeting runs 2-4 December 2026; the decision lands on the final day."""
    found = load_event_calendar(CONFIG).between(date(2026, 12, 1), date(2026, 12, 31))

    assert [event.on_date for event in found] == [date(2026, 12, 4)]


def test_unverified_events_are_loaded_rather_than_dropped() -> None:
    """A gate may treat an unverified date as advisory, but only if it can see
    it. Dropping them would make an incomplete calendar look complete."""
    calendar = load_event_calendar(CONFIG)

    assert set(calendar.unverified()) <= set(calendar.events)


def test_an_unknown_event_kind_fails_loudly(tmp_path: Path) -> None:
    """A typo'd kind silently becoming a catch-all is an event the gates never
    look for."""
    bad = tmp_path / "events.yaml"
    bad.write_text("events:\n  - kind: NOT_A_KIND\n    date: 2026-12-04\n")

    with pytest.raises(ValueError, match="NOT_A_KIND"):
        load_event_calendar(bad)


def test_a_missing_file_fails_loudly() -> None:
    with pytest.raises(FileNotFoundError, match="Event calendar"):
        load_event_calendar(Path("config/absent.yaml"))
