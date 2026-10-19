"""Choosing at most two ideas, and saying why the others did not make it.

**Nothing here is a model.** Ranking is the last place a judgement gets made
before a briefing, so it is the last place that should be improvisable. The
order is a documented rule over measured values, it is deterministic, and
`verify 3_4` runs it three times to prove the order does not move.

**There is no evidence that this ordering predicts anything.** Credibility
ahead of reward-to-risk is a reasonable guess and nothing more - no signal is
backtested (FR-5.10), and ranking has never been scored against outcomes. The
journal records the rank with every idea so that in a hundred resolved ideas
this question becomes answerable. Until then the honest claim is "these two
passed every gate and here is the order we used", not "these are the best two".

**The tie-break is alphabetical and that matters.** Two ideas with identical
measurements must come back in the same order on every run, or the determinism
check in `docs/12` fails for a reason that has nothing to do with the maths.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from trading_desk.domain.enums import Direction, SetupId
from trading_desk.domain.proposals import MAX_PROPOSALS_PER_CYCLE

# Confidence is reported but never calibrated: step 3.8 builds the calibration,
# and until it has run over 50 resolved ideas there is no basis for a number
# above the middle of the range. An uncalibrated 0.9 is a lie with a decimal
# point, so every confidence is capped here and the cap is shown.
UNCALIBRATED_CONFIDENCE_CEILING = 0.60


@dataclass(frozen=True)
class Candidate:
    """An idea that has already passed every gate, ready to be ordered.

    Carries no thesis and no prose: ranking compares numbers, and giving it
    text to look at would be an invitation to start weighing the text.
    """

    symbol: str
    setup_id: SetupId
    direction: Direction
    credibility: float
    reward_to_risk: float
    gate_values: dict[str, str] = field(default_factory=dict)
    used_iv_proxy: bool = False

    def __post_init__(self) -> None:
        if not 0.0 <= self.credibility <= 1.0:
            raise ValueError(f"{self.symbol}: credibility must be 0-1, got {self.credibility}")
        if self.reward_to_risk < 0:
            raise ValueError(
                f"{self.symbol}: reward_to_risk cannot be negative, got {self.reward_to_risk}"
            )

    def confidence(self) -> float:
        """A reported confidence, capped because nothing has calibrated it.

        Deliberately a function of credibility alone. Folding reward-to-risk in
        would make a wide spread look like a *likelier* idea rather than a
        better-paying one, and those are different claims.
        """
        return min(self.credibility, UNCALIBRATED_CONFIDENCE_CEILING)


@dataclass(frozen=True)
class RankedCandidate:
    candidate: Candidate
    rank: int
    why: str


def sort_key(candidate: Candidate) -> tuple[float, float, str]:
    """Credibility first, then reward-to-risk, then the symbol.

    Negated so that `sorted` puts the highest first while the symbol still
    sorts A-Z - which is what makes the order total, and therefore repeatable.
    """
    return (-candidate.credibility, -candidate.reward_to_risk, candidate.symbol)


def rank_candidates(
    candidates: list[Candidate], limit: int = MAX_PROPOSALS_PER_CYCLE
) -> tuple[list[RankedCandidate], list[Candidate]]:
    """The top `limit` in order, and everything that did not fit.

    The overflow is returned rather than dropped: an idea that passed every
    gate and lost on rank is a different thing from an idea that was killed,
    and the briefing shows both (FR-4.10).
    """
    if limit < 0:
        raise ValueError(f"limit cannot be negative, got {limit}")

    ordered = sorted(candidates, key=sort_key)
    ranked = [
        RankedCandidate(candidate=candidate, rank=position, why=_why(candidate, position))
        for position, candidate in enumerate(ordered[:limit], start=1)
    ]
    return ranked, ordered[limit:]


def _why(candidate: Candidate, position: int) -> str:
    return (
        f"rank {position}: credibility {candidate.credibility:.2f}, "
        f"reward:risk {candidate.reward_to_risk:.2f} "
        f"(ordering is a documented rule, not a scored model)"
    )
