"""Loads the liquidity thresholds that decide the universe.

Separate from the ranking logic it feeds, because the thresholds are config with a
review status and the ranking is pure maths. `verified` is carried through rather
than dropped: an unreviewed threshold silently deciding which 25 names the system
watches is the kind of default that becomes permanent by accident.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time
from pathlib import Path

import yaml

from trading_desk.domain.liquidity import LiquidityRules
from trading_desk.domain.sessions import SessionHours
from trading_desk.domain.values import Money

DEFAULT_RULES_PATH = Path("config/exchange_rules.yaml")


@dataclass(frozen=True)
class ExchangeRules:
    """The thresholds, plus whether anyone has signed off on them."""

    liquidity: LiquidityRules
    session: SessionHours
    effective_from: date
    verified: bool
    source: str


def _parse_hhmm(value: str) -> time:
    """Parse "09:15". Quoted in YAML, so it arrives as a str rather than a sexagesimal int."""
    hour, minute = value.split(":")
    return time(int(hour), int(minute))


def load_exchange_rules(path: Path = DEFAULT_RULES_PATH) -> ExchangeRules:
    if not path.is_file():
        raise FileNotFoundError(f"Exchange rules not found at {path}")

    raw = yaml.safe_load(path.read_text())
    section = raw["liquidity"]
    hours = raw["session"]

    # from_rupees takes a str so an accidental float in YAML cannot reach Money.
    turnover = Money.from_rupees(str(section["min_premium_turnover_rupees"]))

    return ExchangeRules(
        liquidity=LiquidityRules(
            min_premium_turnover=turnover,
            min_open_interest=int(section["min_open_interest"]),
            min_priced_fraction=float(section["min_priced_fraction"]),
            max_stocks=int(section["max_stocks"]),
        ),
        session=SessionHours(
            open_ist=_parse_hhmm(hours["open_ist"]),
            close_ist=_parse_hhmm(hours["close_ist"]),
        ),
        effective_from=raw["effective_from"],
        verified=bool(raw.get("verified", False)),
        source=str(raw.get("source", "")),
    )
