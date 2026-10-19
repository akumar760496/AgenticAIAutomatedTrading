"""A record that a scheduled job fired, was skipped, or failed.

Separate from `ports/tracing.py`, which records what an *agent* did inside a
cycle. This records what the *scheduler* did, and the two answer different
questions: "why does this briefing line say that" versus "has this thing been
running".

**SKIPPED is a first-class outcome, not an absence.** A holiday and a crashed
scheduler both leave no briefing, and from the outside they look identical.
Writing "skipped: exchange holiday" is what makes the difference legible the
next morning - and it is the only way the streak arithmetic in
`domain/schedule.py` can tell a quiet Diwali from a dead laptop.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import Protocol


class JobOutcome(StrEnum):
    RAN = "RAN"
    SKIPPED = "SKIPPED"  # not a trading day, or nothing to do
    FAILED = "FAILED"


@dataclass(frozen=True)
class JobRun:
    """One firing of one job.

    `trading_date` is the IST date the run belongs to and is stored alongside
    the instant rather than derived from it. Deriving it means every reader has
    to remember to convert, and the one that forgets reads a 23:30 IST backup
    as the previous day's (rule 5).
    """

    job: str
    outcome: JobOutcome
    at: datetime
    trading_date: date
    detail: str = ""

    def __post_init__(self) -> None:
        if self.at.tzinfo is None:
            raise ValueError(f"{self.job}: a job run must be timestamped tz-aware")
        if not self.job.strip():
            raise ValueError("a job run must name its job")


class JobLogPort(Protocol):
    def record(self, run: JobRun) -> None:
        """Append one run. Never overwrites: the history is the evidence."""
        ...

    def runs(self, job: str, since: date) -> list[JobRun]:
        """One job's runs from `since` onward, oldest first."""
        ...
