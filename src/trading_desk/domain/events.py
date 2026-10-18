"""Scheduled events, and the windows around them.

Dates kill option trades. A long premium position held through a results
announcement loses to the volatility crush even when the direction was right, and
an RBI decision moves every bank name at once. So the gates need to ask "what is
coming for this instrument between these two dates" and get a precise answer.

Pure module: events are supplied, never looked up, and nothing reads a clock.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum


class EventKind(StrEnum):
    """What kind of scheduled event this is.

    EXPIRY is included because it behaves like the others for gating purposes -
    it is a known future date that changes how an option prices - even though it
    comes from the exchange rather than a calendar.
    """

    RESULTS = "RESULTS"
    RBI_POLICY = "RBI_POLICY"
    CPI = "CPI"
    IIP = "IIP"
    FOMC = "FOMC"
    BUDGET = "BUDGET"
    EXPIRY = "EXPIRY"


@dataclass(frozen=True)
class MarketEvent:
    """One dated event.

    `symbol` is None for an event that affects the whole market, such as an RBI
    decision. A gate asking about RELIANCE must see those too, which is why
    filtering is a method here rather than a dict lookup by symbol.

    `verified` travels with the event rather than being checked once at load.
    A gate may reasonably treat an unverified date as advisory, and it can only
    do that if the flag survives the journey.
    """

    kind: EventKind
    on_date: date
    description: str
    symbol: str | None = None
    verified: bool = False
    source: str = ""

    def affects(self, symbol: str) -> bool:
        """Whether this event matters for an instrument. Market-wide events do."""
        return self.symbol is None or self.symbol == symbol

    def days_until(self, as_of: date) -> int:
        """Calendar days from `as_of` to the event. Negative once it has passed."""
        return (self.on_date - as_of).days


def events_between(
    events: Sequence[MarketEvent], start: date, end: date, symbol: str | None = None
) -> list[MarketEvent]:
    """Events in [start, end] inclusive, optionally for one instrument.

    Inclusive at both ends on purpose. The question a gate asks is "is there an
    event between now and expiry", and an event *on* the expiry date is the one
    that matters most - excluding it would silently drop the worst case.
    """
    if start > end:
        raise ValueError(f"start {start} is after end {end}")

    matching = [event for event in events if start <= event.on_date <= end]
    if symbol is not None:
        matching = [event for event in matching if event.affects(symbol)]
    return sorted(matching, key=lambda event: (event.on_date, event.kind))


def within_days(
    events: Sequence[MarketEvent], as_of: date, days: int, symbol: str | None = None
) -> list[MarketEvent]:
    """Events falling in the next `days` days, inclusive of today and the last day."""
    if days < 0:
        raise ValueError(f"days must not be negative, got {days}")
    return events_between(events, as_of, as_of + timedelta(days=days), symbol)
