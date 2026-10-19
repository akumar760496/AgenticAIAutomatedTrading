"""Confidence may not influence size until it has earned the right.

FR-7.8, and the requirement says **enforced in code, not policy** - so this is
a module with a test behind it rather than a line in a document. The rule:

    confidence-based sizing is blocked until calibration beats a naive
    base-rate baseline over at least 50 resolved ideas

Why 50 and why a baseline. An agent's confidence will cluster at 0.7-0.8
(`docs/12` §6 says to expect it, and says it is not a bug). Numbers in that
range look authoritative and, before any outcomes exist, carry no information
whatsoever. Sizing on them would convert a presentation artefact into real
money at risk, and would do it in the direction of the ideas the model happens
to like - which is the single most expensive mistake available here.

**This is a lock, not a calculation.** Nothing in this module returns a size,
a quantity or a multiplier, and nothing in `domain/risk/` consumes one. When
the lock opens, that is the beginning of a conversation about sizing, not an
automatic switch - which is why `is_open` is reported to a human and never
wired to anything.

Rule 9: the check returns what it measured and the threshold, so "why is sizing
still flat?" is answered by a number and not by a shrug.
"""

from __future__ import annotations

from dataclasses import dataclass

from trading_desk.domain.calibration import Calibration

# The sample floor. Fifty resolved ideas is not a lot of statistics; it is the
# point at which a Brier comparison stops being dominated by which three trades
# happened to land first.
MIN_RESOLVED_IDEAS = 50


@dataclass(frozen=True)
class SizingLock:
    """Whether confidence may inform size, and the numbers behind the answer."""

    resolved_count: int
    required_count: int
    brier: float | None
    baseline_brier: float | None

    @property
    def has_enough_samples(self) -> bool:
        return self.resolved_count >= self.required_count

    @property
    def beats_baseline(self) -> bool:
        if self.brier is None or self.baseline_brier is None:
            return False
        return self.brier < self.baseline_brier

    @property
    def is_open(self) -> bool:
        """Both conditions, never one. Sample count alone is how this gets gamed."""
        return self.has_enough_samples and self.beats_baseline

    def describe(self) -> str:
        if not self.has_enough_samples:
            return (
                f"locked: {self.resolved_count} resolved ideas, "
                f"need {self.required_count} (calibration not yet measurable)"
            )
        if self.brier is None or self.baseline_brier is None:
            return (
                f"locked: {self.resolved_count} resolved ideas but no calibration "
                f"has been computed over them"
            )
        if not self.beats_baseline:
            return (
                f"locked: Brier {self.brier:.4f} does not beat baseline "
                f"{self.baseline_brier:.4f} over {self.resolved_count} resolved ideas"
            )
        return (
            f"open for discussion: Brier {self.brier:.4f} beats baseline "
            f"{self.baseline_brier:.4f} over {self.resolved_count} resolved ideas. "
            f"Sizing is still flat until you change it deliberately."
        )


def sizing_lock(
    calibration: Calibration | None, *, required: int = MIN_RESOLVED_IDEAS
) -> SizingLock:
    """The lock's state. `None` calibration means nothing has been measured yet.

    A missing calibration is not treated as a failing one with a bad score, and
    not as a passing one either - it keeps the lock shut and says which of the
    two reasons applies, because "we have not looked" and "we looked and it is
    no better than guessing" call for different responses.
    """
    if calibration is None:
        return SizingLock(
            resolved_count=0, required_count=required, brier=None, baseline_brier=None
        )
    return SizingLock(
        resolved_count=calibration.sample_count,
        required_count=required,
        brier=calibration.brier,
        baseline_brier=calibration.baseline_brier,
    )
