"""What the research system is allowed to hand you. Never an order.

**This module is where CLAUDE.md rule 1 is made structural.** A `TradeProposal`
has no quantity, no lot count, no order type and no order price, and a test
asserts that by introspecting every type reachable from it - so a future
"helpful" addition fails CI rather than review (FR-4.7).

The distinction that keeps tripping people up, stated once:

- A **strike** and a **level** describe the idea. "Buy the 1400 call, stop
  below 1380" is research. Both are needed for the briefing to be readable.
- A **quantity** or an **order price** turn the idea into an instruction. That
  is the line, and nothing in this module crosses it.

So the fields are named to stay clearly on the research side - `trigger_level`,
not `trigger_price` - and the banned-substring list in the test includes
`price` on purpose. If a legitimate field ever needs the word, that is a
conversation, not a rename.

At most two proposals per cycle, enforced by `rank: Literal[1, 2]` plus a
runtime check, because a dataclass does not police a Literal (FR-4.8).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal
from uuid import UUID

from trading_desk.domain.enums import Direction, Horizon, OptionRight, SetupId
from trading_desk.domain.research import Thesis
from trading_desk.domain.values import Price

MAX_PROPOSALS_PER_CYCLE = 2


class LegAction(StrEnum):
    """Which way a leg goes. Not an order type - a vertical has one of each."""

    BUY = "BUY"
    SELL = "SELL"


class StopMethod(StrEnum):
    """How a stop level was derived. Recorded so the journal can score it."""

    ATR_MULTIPLE = "ATR_MULTIPLE"
    STRUCTURE = "STRUCTURE"  # below a swing low, above a swing high
    PERCENT = "PERCENT"
    THESIS_INVALIDATION = "THESIS_INVALIDATION"


class TargetMethod(StrEnum):
    ATR_MULTIPLE = "ATR_MULTIPLE"
    STRUCTURE = "STRUCTURE"
    RANGE_PROJECTION = "RANGE_PROJECTION"


@dataclass(frozen=True)
class EntryCondition:
    """When the idea becomes live. `trigger_level` may be None for "at open"."""

    description: str
    trigger_level: Price | None = None

    def __post_init__(self) -> None:
        if not self.description.strip():
            raise ValueError("an entry condition must describe itself")


@dataclass(frozen=True)
class StopBasis:
    """Where the idea is abandoned, and how that level was arrived at.

    `method` and `multiple` are kept alongside the level so the journal can ask
    later whether 1.5x ATR stops were too tight. Storing only the level would
    make that question unanswerable.
    """

    method: StopMethod
    level: Price
    multiple: Decimal | None = None
    rationale: str = ""


@dataclass(frozen=True)
class TargetBasis:
    method: TargetMethod
    level: Price
    multiple: Decimal | None = None
    rationale: str = ""


@dataclass(frozen=True)
class ProposedLeg:
    """One leg of a structure: which option, bought or sold.

    **No quantity and no ratio.** v1 trades 1:1 verticals and single legs, so a
    ratio field would buy nothing and would be the obvious place for a lot
    count to arrive later wearing a different hat. When a ratioed structure is
    genuinely needed, adding it is a deliberate decision with a DEVLOG entry.
    """

    right: OptionRight
    action: LegAction
    strike: Price
    expiry: date
    delta: float | None = None

    def __post_init__(self) -> None:
        if self.delta is not None and not -1.0 <= self.delta <= 1.0:
            raise ValueError(f"delta must be -1 to 1, got {self.delta}")


@dataclass(frozen=True)
class TradeProposal:
    """One ranked idea. Research output, not an instruction.

    Everything needed to understand and judge the idea; nothing that could
    place it. See the module docstring.
    """

    proposal_id: UUID
    rank: Literal[1, 2]
    instrument_symbol: str
    setup_id: SetupId
    direction: Direction
    horizon: Horizon
    thesis: Thesis
    legs: tuple[ProposedLeg, ...]
    entry: EntryCondition
    stop: StopBasis
    target: TargetBasis
    created_at: datetime
    # Every gate's measured value, keyed by gate name. Values not verdicts, so
    # the journal can ask which gate was predictive (rule 9).
    gate_values: dict[str, str] = field(default_factory=dict)
    used_iv_proxy: bool = False

    def __post_init__(self) -> None:
        # A dataclass does not enforce Literal at runtime, and mypy only sees
        # the call sites it can type. An LLM-built proposal arrives as data.
        if self.rank not in (1, 2):
            raise ValueError(f"{self.instrument_symbol}: rank must be 1 or 2, got {self.rank}")
        if not self.legs:
            raise ValueError(f"{self.instrument_symbol}: a proposal needs at least one leg")
        if self.created_at.tzinfo is None:
            raise ValueError(f"{self.instrument_symbol}: created_at must be tz-aware")
        if self.thesis.instrument_symbol != self.instrument_symbol:
            raise ValueError(
                f"{self.instrument_symbol}: thesis is about "
                f"{self.thesis.instrument_symbol}, not this instrument"
            )
        if self.thesis.direction is not self.direction:
            raise ValueError(
                f"{self.instrument_symbol}: proposal is {self.direction} but its thesis "
                f"is {self.thesis.direction}"
            )


@dataclass(frozen=True)
class RejectedCandidate:
    """An idea that did not make it, and the gate that killed it.

    Returned with the result rather than dropped. The rejected list is how you
    find out a gate is killing everything, which is invisible if only the
    survivors are reported.
    """

    instrument_symbol: str
    killed_by: str
    measured: str
    threshold: str

    def __post_init__(self) -> None:
        if not self.killed_by.strip():
            raise ValueError(f"{self.instrument_symbol}: a rejection must name a gate")


@dataclass(frozen=True)
class ResearchCycleResult:
    """One cycle's output: at most two ideas, or a stated reason for none.

    "No ideas today" is a first-class result, not an empty list. An empty list
    with no reason is indistinguishable from a crashed cycle, and the honest
    answer on a quiet day is silence *with* an explanation.
    """

    cycle_id: UUID
    generated_at: datetime
    proposals: tuple[TradeProposal, ...] = ()
    rejected: tuple[RejectedCandidate, ...] = ()
    no_ideas_reason: str | None = None

    def __post_init__(self) -> None:
        if self.generated_at.tzinfo is None:
            raise ValueError(f"{self.cycle_id}: generated_at must be tz-aware")

        if len(self.proposals) > MAX_PROPOSALS_PER_CYCLE:
            raise ValueError(
                f"{self.cycle_id}: at most {MAX_PROPOSALS_PER_CYCLE} proposals per cycle, "
                f"got {len(self.proposals)}"
            )

        ranks = [proposal.rank for proposal in self.proposals]
        if len(set(ranks)) != len(ranks):
            raise ValueError(f"{self.cycle_id}: two proposals share a rank: {ranks}")

        if not self.proposals and not self.no_ideas_reason:
            raise ValueError(
                f"{self.cycle_id}: a cycle with no proposals must say why - an empty "
                "list without a reason looks identical to a crash"
            )

    @property
    def has_ideas(self) -> bool:
        return bool(self.proposals)

    def ranked(self) -> tuple[TradeProposal, ...]:
        return tuple(sorted(self.proposals, key=lambda proposal: proposal.rank))
