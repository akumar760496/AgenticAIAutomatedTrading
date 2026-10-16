"""Which days the market is open.

Deliberately conservative. A day wrongly marked as a holiday costs one wasted
capture attempt. A day wrongly marked as open means a *missed* capture, and a
missed capture is IV history that cannot be recovered - so unknown days are
treated as trading days and the capture simply fails harmlessly if they are not.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import yaml

DEFAULT_HOLIDAYS_PATH = Path("config/holidays.yaml")

SATURDAY = 5
SUNDAY = 6


@dataclass(frozen=True)
class TradingCalendar:
    """Weekends plus a configured holiday list."""

    holidays: frozenset[date]
    special_sessions: frozenset[date]
    verified: bool

    def is_trading_day(self, day: date) -> bool:
        if day.weekday() in (SATURDAY, SUNDAY):
            return False
        return day not in self.holidays

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
    for key, value in raw.items():
        if isinstance(key, int) and isinstance(value, list):  # a year block
            holidays.update(_parse_date(entry["date"]) for entry in value)

    special = {_parse_date(entry["date"]) for entry in raw.get("special_sessions", [])}

    return TradingCalendar(
        holidays=frozenset(holidays),
        special_sessions=frozenset(special),
        verified=bool(raw.get("verified", False)),
    )


def _parse_date(value: object) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))
