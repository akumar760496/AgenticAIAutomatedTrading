"""Whether the confidence numbers mean anything.

A Brier score on its own is a number with no sense of scale, so everything here
is reported against a **naive base-rate baseline**: a forecaster that ignores
every input and always predicts the historical success rate. Beating that is the
minimum bar for confidence being information rather than decoration, and it is
the bar `domain/sizing_lock.py` enforces (FR-7.7, FR-7.8).

The decomposition is Murphy's, and it is here because the single number hides
the thing you actually want to know:

    brier = reliability - resolution + uncertainty

- **reliability** - how far each confidence band's claim was from what happened.
  Zero is perfect. This is the calibration curve, as one number.
- **resolution** - how far the bands' outcomes spread away from the base rate.
  Zero means the confidence told you nothing, even if it was well calibrated.
- **uncertainty** - the base rate's own variance. Not the model's fault and not
  improvable; it is why a Brier score cannot be compared across datasets.

**The failure this catches:** an agent whose confidence always clusters at
0.7-0.8 (which `docs/12` §6 says to expect) can be perfectly *calibrated* and
still useless, because resolution is zero - it is just reporting the base rate
in a louder voice. `beats_baseline` is false in that case, and the sizing lock
stays shut.

**Success is net money, not the label.** A `TIME_STOP` that netted a profit is a
success; a `TARGET` that netted a loss once costs were paid is not. Scoring the
label would measure whether the levels were reached, which is a different and
less useful question than whether the idea made money (ISS-023: costs are ~13%
of a vertical's best case, so the two genuinely disagree).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

# Deciles. Ten bands over ~50 resolved ideas leaves most bands near-empty, which
# is honest - an empty band is reported as empty rather than smoothed into its
# neighbour, because smoothing invents evidence.
BIN_COUNT = 10

# Below this a band's observed rate is noise, and the report says so rather than
# printing a rate derived from two trades.
MIN_BIN_SAMPLES = 5


@dataclass(frozen=True)
class ResolvedIdea:
    """One scored idea, reduced to the two numbers calibration needs."""

    confidence: float
    succeeded: bool

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence must be 0 to 1, got {self.confidence}")


@dataclass(frozen=True)
class ReliabilityBin:
    """One confidence band: what was claimed, and what happened."""

    lower: float
    upper: float
    count: int
    mean_confidence: float
    observed_rate: float

    @property
    def is_meaningful(self) -> bool:
        return self.count >= MIN_BIN_SAMPLES

    @property
    def gap(self) -> float:
        """Claimed minus observed. Positive is overconfident."""
        return self.mean_confidence - self.observed_rate

    def describe(self) -> str:
        label = f"{self.lower:.0%}-{self.upper:.0%}"
        if not self.count:
            return f"{label}: empty"
        body = (
            f"{label}: {self.count} idea(s), claimed {self.mean_confidence:.1%}, "
            f"observed {self.observed_rate:.1%}, gap {self.gap:+.1%}"
        )
        if not self.is_meaningful:
            return f"{body} (under {MIN_BIN_SAMPLES} - treat as noise)"
        return body


@dataclass(frozen=True)
class Calibration:
    """The full picture for one window of resolved ideas."""

    sample_count: int
    base_rate: float
    brier: float
    baseline_brier: float
    reliability: float
    resolution: float
    uncertainty: float
    bins: tuple[ReliabilityBin, ...]

    @property
    def skill(self) -> float:
        """Fraction of the baseline's error removed. Negative is worse than guessing."""
        if self.baseline_brier == 0.0:
            # Every outcome identical, so the base rate is 0 or 1 and predicts
            # perfectly. There is no error to remove and no skill to claim.
            return 0.0
        return 1.0 - (self.brier / self.baseline_brier)

    @property
    def beats_baseline(self) -> bool:
        return self.brier < self.baseline_brier

    def summary(self) -> str:
        verdict = "beats" if self.beats_baseline else "does not beat"
        return (
            f"{self.sample_count} resolved, base rate {self.base_rate:.1%}: "
            f"Brier {self.brier:.4f} {verdict} baseline {self.baseline_brier:.4f} "
            f"(skill {self.skill:+.1%}); reliability {self.reliability:.4f}, "
            f"resolution {self.resolution:.4f}, uncertainty {self.uncertainty:.4f}"
        )

    def as_stored_bins(self) -> dict[str, str]:
        """The reliability curve as the journal stores it: text, one row per band."""
        return {f"{b.lower:.1f}-{b.upper:.1f}": b.describe() for b in self.bins}


def calibrate(ideas: tuple[ResolvedIdea, ...]) -> Calibration:
    """Brier, its decomposition, and the reliability curve.

    Raises on an empty set rather than returning zeros. A Brier score of 0.0 is
    a perfect forecaster, and that is exactly what "no data" must not look like
    (rule 6).
    """
    if not ideas:
        raise ValueError("calibration needs at least one resolved idea")

    count = len(ideas)
    outcomes = [1.0 if idea.succeeded else 0.0 for idea in ideas]
    base_rate = sum(outcomes) / count

    brier = sum((idea.confidence - o) ** 2 for idea, o in zip(ideas, outcomes, strict=True)) / count
    baseline_brier = sum((base_rate - o) ** 2 for o in outcomes) / count

    bins = _bins(ideas)
    # Murphy's decomposition, over the same bins the curve is reported on. Using
    # different bins for the maths and the report would let the two disagree.
    reliability = 0.0
    resolution = 0.0
    for band in bins:
        if not band.count:
            continue
        weight = band.count / count
        reliability += weight * (band.mean_confidence - band.observed_rate) ** 2
        resolution += weight * (band.observed_rate - base_rate) ** 2

    return Calibration(
        sample_count=count,
        base_rate=base_rate,
        brier=brier,
        baseline_brier=baseline_brier,
        reliability=reliability,
        resolution=resolution,
        uncertainty=base_rate * (1.0 - base_rate),
        bins=bins,
    )


def _bins(ideas: tuple[ResolvedIdea, ...]) -> tuple[ReliabilityBin, ...]:
    """Deciles, every one reported, including the empty ones."""
    buckets: list[list[ResolvedIdea]] = [[] for _ in range(BIN_COUNT)]
    for idea in ideas:
        # A confidence of exactly 1.0 belongs in the top band, not in an
        # eleventh one that does not exist.
        index = min(int(idea.confidence * BIN_COUNT), BIN_COUNT - 1)
        buckets[index].append(idea)

    bands = []
    for index, bucket in enumerate(buckets):
        lower = index / BIN_COUNT
        upper = (index + 1) / BIN_COUNT
        if not bucket:
            bands.append(ReliabilityBin(lower, upper, 0, 0.0, 0.0))
            continue
        bands.append(
            ReliabilityBin(
                lower=lower,
                upper=upper,
                count=len(bucket),
                mean_confidence=sum(i.confidence for i in bucket) / len(bucket),
                observed_rate=sum(1 for i in bucket if i.succeeded) / len(bucket),
            )
        )
    return tuple(bands)


def succeeded(net_pnl_paise: int) -> bool:
    """Whether an outcome counts as a success. Net money, not the label.

    A separate function with its own name because it is a definition, and a
    definition buried in a comprehension is one nobody finds when they come to
    argue with it. Breakeven is not a success: zero means the costs ate it.
    """
    return net_pnl_paise > 0


def decimal_rate(value: float) -> Decimal:
    """A rate as a Decimal, for storing next to money without float drift."""
    return Decimal(str(round(value, 6)))
