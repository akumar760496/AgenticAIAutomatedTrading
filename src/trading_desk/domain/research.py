"""Evidence and theses: what an agent is allowed to claim, and on what basis.

The type does the arguing. An agent cannot construct a `Thesis` from a single
source, so "two independent sources" (FR-4.6) is not a prompt instruction an
LLM might drift away from on a tired Tuesday - it is a constructor that raises.

**What "independent" means here, and what it does not.** Independence is
counted by distinct `source_name`, not by number of evidence items: five
quotes from one publisher are one source. That is necessary but not
sufficient, because five outlets carrying the same wire story are also not
independent, and this type cannot see that. Syndication is caught upstream by
`domain.similarity` grouping on `event_key`; what is enforced here is the
floor, not the whole idea.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from trading_desk.domain.enums import Direction, Horizon, SourceTier

# Below this, a claim is too weak to count toward the two-source floor. Kept
# deliberately low: the point is to exclude a passing mention, not to pre-judge
# the evidence, which is the credibility gate's job in step 2.3.
MIN_EVIDENCE_STRENGTH = 0.1

MIN_INDEPENDENT_SOURCES = 2


@dataclass(frozen=True)
class Evidence:
    """One claim, with where it came from and how strongly it points (FR-4.5).

    `strength` is the agent's own read of how forceful this item is, 0 to 1. It
    is not credibility - that comes from `source_tier` and is scored by a
    documented rule, not by the agent's opinion. Keeping them separate means a
    confident agent cannot talk a weak source up.
    """

    claim: str
    source_name: str  # the publisher or exchange - the unit of independence
    source_ref: str  # the specific article, filing or snapshot
    source_tier: SourceTier
    observed_at: datetime
    direction: Direction
    strength: float

    def __post_init__(self) -> None:
        if not self.claim.strip():
            raise ValueError("evidence must carry a claim")
        if not self.source_name.strip():
            raise ValueError(f"{self.claim[:40]!r}: evidence must name its source")
        if self.observed_at.tzinfo is None:
            raise ValueError(f"{self.claim[:40]!r}: observed_at must be tz-aware")
        if not 0.0 <= self.strength <= 1.0:
            raise ValueError(f"{self.claim[:40]!r}: strength must be 0-1, got {self.strength}")


@dataclass(frozen=True)
class Thesis:
    """A claim about an instrument, and the evidence it rests on.

    Requires two independent sources (FR-4.6) and an invalidation condition.
    The invalidation is mandatory for the same reason as the evidence floor: an
    idea that cannot be wrong cannot be scored, and the journal exists to score
    ideas.
    """

    instrument_symbol: str
    claim: str
    direction: Direction
    horizon: Horizon
    evidence: tuple[Evidence, ...]
    # What would make this wrong. Free text, but not optional.
    invalidation: str
    confidence: float
    created_at: datetime

    def __post_init__(self) -> None:
        if not self.claim.strip():
            raise ValueError(f"{self.instrument_symbol}: a thesis must carry a claim")
        if not self.invalidation.strip():
            raise ValueError(
                f"{self.instrument_symbol}: a thesis must say what would make it wrong"
            )
        if self.created_at.tzinfo is None:
            raise ValueError(f"{self.instrument_symbol}: created_at must be tz-aware")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(
                f"{self.instrument_symbol}: confidence must be 0-1, got {self.confidence}"
            )

        sources = self.independent_sources()
        if len(sources) < MIN_INDEPENDENT_SOURCES:
            raise ValueError(
                f"{self.instrument_symbol}: a thesis needs {MIN_INDEPENDENT_SOURCES} "
                f"independent sources, got {len(sources)} ({sorted(sources)})"
            )

    def independent_sources(self) -> set[str]:
        """Distinct source names among evidence strong enough to count."""
        return {
            item.source_name.strip().upper()
            for item in self.evidence
            if item.strength >= MIN_EVIDENCE_STRENGTH
        }

    def supporting(self) -> tuple[Evidence, ...]:
        """Evidence pointing the same way as the thesis."""
        return tuple(item for item in self.evidence if item.direction is self.direction)

    def contradicting(self) -> tuple[Evidence, ...]:
        """Evidence pointing the other way, kept rather than discarded.

        An agent that drops inconvenient evidence produces a thesis that looks
        stronger than it is. The briefing shows both sides.
        """
        opposing = {Direction.BULLISH: Direction.BEARISH, Direction.BEARISH: Direction.BULLISH}
        against = opposing.get(self.direction)
        if against is None:
            return ()
        return tuple(item for item in self.evidence if item.direction is against)
