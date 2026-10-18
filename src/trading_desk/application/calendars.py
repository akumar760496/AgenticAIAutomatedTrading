"""Which days the market is open.

Deliberately conservative. A day wrongly marked as a holiday costs one wasted
capture attempt. A day wrongly marked as open means a *missed* capture, and a
missed capture is IV history that cannot be recovered - so unknown days are
treated as trading days and the capture simply fails harmlessly if they are not.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import yaml

DEFAULT_HOLIDAYS_PATH = Path("config/holidays.yaml")

SATURDAY = 5
SUNDAY = 6


@dataclass(frozen=True)
class HolidayEvidence:
    """Why a date is believed to be a holiday.

    EXPIRY_SHIFT is primary: the exchange's own weekly expiry list skipped that
    Tuesday. PUBLISHED is a secondary calendar - believed, not proven.
    """

    EXPIRY_SHIFT = "expiry_shift"
    PUBLISHED = "published"


@dataclass(frozen=True)
class TradingCalendar:
    """Weekends plus a configured holiday list."""

    holidays: frozenset[date]
    special_sessions: frozenset[date]
    verified: bool
    holiday_evidence: dict[date, str] = field(default_factory=dict)

    def is_trading_day(self, day: date) -> bool:
        if day.weekday() in (SATURDAY, SUNDAY):
            return False
        return day not in self.holidays

    def evidence_for(self, day: date) -> str | None:
        """How this date came to be listed, or None if it is not a holiday."""
        return self.holiday_evidence.get(day)

    def missing_holidays(self, displaced_tuesdays: Iterable[date]) -> list[date]:
        """Tuesdays the exchange treated as shut that this calendar does not list.

        The expiry list is primary evidence from the venue, so a date here is a
        gap in the holiday file rather than a disagreement. This is what found
        2026-11-24 after it had been missing unnoticed.
        """
        return sorted(day for day in displaced_tuesdays if day not in self.holidays)

    def reason_closed(self, day: date) -> str | None:
        """Why the market is shut, or None when it is open."""
        if day.weekday() == SATURDAY:
            return "Saturday"
        if day.weekday() == SUNDAY:
            return "Sunday"
        if day in self.holidays:
            return "exchange holiday"
        return None


def load_trading_calendar(path: Path = DEFAULT_HOLIDAYS_PATH) -> TradingCalendar:
    if not path.is_file():
        raise FileNotFoundError(f"Holiday calendar not found at {path}")

    raw = yaml.safe_load(path.read_text())

    holidays: set[date] = set()
    evidence: dict[date, str] = {}
    for key, value in raw.items():
        if not (isinstance(key, int) and isinstance(value, list)):  # a year block
            continue
        for entry in value:
            day = _parse_date(entry["date"])
            holidays.add(day)
            evidence[day] = str(entry.get("evidence", HolidayEvidence.PUBLISHED))

    special = {_parse_date(entry["date"]) for entry in raw.get("special_sessions", [])}

    return TradingCalendar(
        holidays=frozenset(holidays),
        special_sessions=frozenset(special),
        verified=bool(raw.get("verified", False)),
        holiday_evidence=evidence,
    )


def _parse_date(value: object) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))
