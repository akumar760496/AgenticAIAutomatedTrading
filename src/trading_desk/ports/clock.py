"""The clock, as a dependency.

Time is an input, not an ambient fact. Code that reads the system clock directly
cannot be tested at a market open, at expiry, or across a session boundary without
waiting for one. Code that takes a Clock can be tested at any instant.

Every component that needs the time takes a Clock. Domain functions do not even do
that - they take the timestamp itself as an argument.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol


class Clock(Protocol):
    """Returns the current time. Always tz-aware UTC."""

    def utc_now(self) -> datetime:
        """The current instant, tz-aware, in UTC. Never naive, never local."""
        ...
