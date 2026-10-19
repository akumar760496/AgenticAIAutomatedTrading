"""The sizing lock, and the structural proof that confidence cannot reach size.

FR-7.8 says "enforced in code, not policy", so there are two kinds of test here:
the lock's own arithmetic, and
`TestConfidenceCannotReachSizing`, which introspects the types the way step
2.1's no-quantity test does. The second kind is the one that fails when somebody
helpfully adds a field.
"""

from __future__ import annotations

import dataclasses
import inspect

from trading_desk.domain.calibration import ResolvedIdea, calibrate
from trading_desk.domain.risk import gate as risk_gate
from trading_desk.domain.risk import sizing as risk_sizing
from trading_desk.domain.sizing_lock import MIN_RESOLVED_IDEAS, sizing_lock


def resolved(count: int, *, confidence: float, win_rate: float) -> tuple[ResolvedIdea, ...]:
    wins = int(count * win_rate)
    return tuple(ResolvedIdea(confidence, index < wins) for index in range(count))


def discriminating(count: int) -> tuple[ResolvedIdea, ...]:
    """Half the ideas at 0.9 winning 90%, half at 0.3 winning 30%.

    A forecaster that genuinely separates winners from losers, which is the only
    kind that beats the baseline.
    """
    half = count // 2
    return resolved(half, confidence=0.9, win_rate=0.9) + resolved(
        count - half, confidence=0.3, win_rate=0.3
    )


class TestTheLockStaysShut:
    def test_with_nothing_measured_at_all(self) -> None:
        lock = sizing_lock(None)
        assert not lock.is_open
        assert lock.resolved_count == 0
        assert "never" not in lock.describe()
        assert "not yet measurable" in lock.describe()

    def test_with_a_good_score_over_too_few_ideas(self) -> None:
        """Beating the baseline over 20 trades is noise, and the lock says so
        by naming both numbers rather than just refusing."""
        lock = sizing_lock(calibrate(discriminating(20)))
        assert lock.beats_baseline
        assert not lock.has_enough_samples
        assert not lock.is_open
        assert "20 resolved" in lock.describe()
        assert str(MIN_RESOLVED_IDEAS) in lock.describe()

    def test_with_enough_ideas_but_no_better_than_guessing(self) -> None:
        """The failure docs/12 warns about: confidence clustered at 0.7, well
        calibrated, carrying no information."""
        lock = sizing_lock(calibrate(resolved(100, confidence=0.7, win_rate=0.7)))
        assert lock.has_enough_samples
        assert not lock.beats_baseline
        assert not lock.is_open
        assert "does not beat baseline" in lock.describe()

    def test_the_two_conditions_are_both_required(self) -> None:
        """Sample count alone is how this would get gamed."""
        enough_but_useless = sizing_lock(calibrate(resolved(60, confidence=0.7, win_rate=0.7)))
        good_but_thin = sizing_lock(calibrate(discriminating(10)))
        assert enough_but_useless.has_enough_samples
        assert good_but_thin.beats_baseline
        assert not enough_but_useless.is_open
        assert not good_but_thin.is_open


class TestTheLockOpens:
    def test_with_enough_ideas_and_real_discrimination(self) -> None:
        lock = sizing_lock(calibrate(discriminating(60)))
        assert lock.is_open
        assert lock.resolved_count == 60

    def test_and_even_then_it_says_sizing_is_still_flat(self) -> None:
        """Opening the lock starts a conversation; it does not change a size.

        Nothing in the codebase consumes `is_open`, and the message is written
        so that is obvious to whoever reads it first.
        """
        described = sizing_lock(calibrate(discriminating(60))).describe()
        assert "open for discussion" in described
        assert "still flat until you change it deliberately" in described


class TestConfidenceCannotReachSizing:
    """The structural half of FR-7.8: not "we did not wire it", but "it cannot
    be wired without this test failing"."""

    def test_the_lock_exposes_no_size_multiplier_or_quantity(self) -> None:
        banned = ("size", "quantity", "qty", "lots", "multiplier", "allocation")
        fields = set(sizing_lock(None).__dataclass_fields__)
        attributes = {name for name in dir(sizing_lock(None)) if not name.startswith("_")}

        for name in fields | attributes:
            for word in banned:
                assert word not in name.lower(), (
                    f"SizingLock.{name} contains {word!r}. This module answers "
                    f"whether confidence has earned the right to inform size; it "
                    f"must never carry a size itself (rule 1, FR-7.8)."
                )

    def test_neither_the_risk_gate_nor_the_sizer_takes_a_confidence_argument(self) -> None:
        """The other end of the same wire, and the direct target.

        `domain/risk/sizing.py` is the module that decides how large a
        hypothetical position is. If it ever grows a confidence parameter, this
        fails before anything reaches production. The gate is checked too,
        because a gate that received confidence could pass it along.
        """
        for module in (risk_gate, risk_sizing):
            for name, function in inspect.getmembers(module, inspect.isfunction):
                if function.__module__ != module.__name__:
                    continue  # imported helper, not this module's own code
                parameters = set(inspect.signature(function).parameters)
                assert "confidence" not in parameters, (
                    f"{module.__name__}.{name} takes a confidence argument. "
                    f"Sizing may not consume confidence until the lock is open "
                    f"and you change this deliberately (FR-7.8)."
                )

    def test_no_sizing_type_carries_a_confidence_field(self) -> None:
        """A dataclass field is the other way confidence could arrive."""
        for name, kind in inspect.getmembers(risk_sizing, inspect.isclass):
            if not dataclasses.is_dataclass(kind) or kind.__module__ != risk_sizing.__name__:
                continue
            fields = {field.name for field in dataclasses.fields(kind)}
            assert "confidence" not in fields, (
                f"risk.sizing.{name} has a confidence field (FR-7.8)."
            )
