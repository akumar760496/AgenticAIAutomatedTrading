"""Loads scheduled events and answers "what is coming" for an instrument.

Kept apart from the trading calendar, which answers a different question. The
trading calendar says whether the market is open; this says what is going to
happen while it is. The gates read both and they fail for different reasons: a
wrong holiday wastes a capture, a wrong results date puts a position through an
earnings move.

Unverified events are loaded rather than dropped. A gate may reasonably treat an
unverified date as advisory - but only if it can see it, and see that it is
unverified. Silently excluding them would make an incomplete calendar look
complete.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import yaml

from trading_desk.domain.events import EventKind, MarketEvent, events_between, within_days

DEFAULT_EVENTS_PATH = Path("config/events.yaml")


@dataclass(frozen=True)
class EventCalendar:
    """Every known scheduled event, queryable by date range and instrument."""

    events: tuple[MarketEvent, ...]

    def between(self, start: date, end: date, symbol: str | None = None) -> list[MarketEvent]:
        return events_between(self.events, start, end, symbol)

    def within(self, as_of: date, days: int, symbol: str | None = None) -> list[MarketEvent]:
        return within_days(self.events, as_of, days, symbol)

    def unverified(self) -> tuple[MarketEvent, ...]:
        """Events whose date has not been traced to the issuing authority."""
        return tuple(event for event in self.events if not event.verified)

    def of_kind(self, kind: EventKind) -> tuple[MarketEvent, ...]:
        return tuple(event for event in self.events if event.kind is kind)


def load_event_calendar(path: Path = DEFAULT_EVENTS_PATH) -> EventCalendar:
    if not path.is_file():
        raise FileNotFoundError(f"Event calendar not found at {path}")

    raw = yaml.safe_load(path.read_text()) or {}
    entries = raw.get("events") or []

    events = []
    for entry in entries:
        events.append(
            MarketEvent(
                # An unrecognised kind raises rather than defaulting to a
                # catch-all: a typo'd event that silently becomes "OTHER" is an
                # event the gates will not look for.
                kind=EventKind(entry["kind"]),
                on_date=_parse_date(entry["date"]),
                description=str(entry.get("description", "")),
                symbol=entry.get("symbol"),
                verified=bool(entry.get("verified", False)),
                source=str(entry.get("source", "")),
            )
        )

    return EventCalendar(events=tuple(events))


def _parse_date(value: object) -> date:
    """YAML parses an unquoted date for us; a quoted one arrives as a string."""
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))
