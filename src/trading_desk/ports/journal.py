"""What we need to record what happened.

The journal is the project's evidence base. Every idea the system produces,
every decision the user makes, every outcome, and - the most valuable field in
the schema - the user's reason for overriding.

**Append-only, and a correction is a new row.** This is the one place the
contract reads oddly at first: `record_decision` called twice for the same idea
is *not* an error. Changing your mind is expected, and the history of changing
it is itself evidence - "I skipped it, then took it an hour later" says
something that an overwritten row destroys. Reads return the latest row. The
rule the adapter must never break is that nothing is ever UPDATEd or DELETEd,
and the database enforces that with a trigger rather than trusting the adapter.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Protocol
from uuid import UUID

from trading_desk.domain.enums import Direction, OptionRight
from trading_desk.domain.proposals import LegAction
from trading_desk.domain.values import Money, Price


@dataclass(frozen=True)
class RecordedLeg:
    """One leg of a briefed structure, with the premium it was quoted at.

    Added in step 3.8, and the premium is the part that needs justifying.
    `ProposedLeg` has no premium on purpose - a proposal is research and a price
    on it starts to look like an instruction (rule 1). But a P&L cannot be
    computed from the prose "bull call spread 1400/1440", so the premium the
    chain printed at briefing time is recorded **here**, on the journal, which
    is a record of what happened rather than a thing the system hands you.

    `entry_premium` is the mid of the leg's bid and ask at briefing time. The
    mid, not the last trade: an illiquid strike's last print can be hours old
    and on the wrong side, and ISS-007 is that failure in its other form.
    """

    right: OptionRight
    action: LegAction
    strike: Price
    expiry: date
    entry_premium: Money

    def __post_init__(self) -> None:
        if self.entry_premium.paise <= 0:
            raise ValueError(f"{self.strike}: a leg's entry premium must be positive")


@dataclass(frozen=True)
class RecordedIdea:
    """An idea as the briefing presented it, with every gate value it was judged on."""

    idea_id: UUID
    cycle_id: UUID
    symbol: str
    setup_id: str
    direction: Direction
    rank: int
    confidence: float
    # Every gate's *measured value*, not its verdict. Storing PASS/FAIL discards
    # the number, and the number is what answers "which gate actually predicted
    # anything?" a year from now (CLAUDE.md rule 9).
    gate_values: dict[str, str]
    hypothetical_risk: Money
    created_at: datetime
    structure: str = ""
    invalidation: str = ""
    agent_rationale: str = ""
    # True when iv_rank came from the realised-vol proxy rather than real IV
    # history. Without it, months of proxy-based ideas are indistinguishable
    # from real ones in the calibration maths.
    used_iv_proxy: bool = False
    # The three numbers an idea has to be reviewed against, added in step 3.7.
    # They were already on `TradeProposal` and were stored only as English
    # inside `gate_values` ("below the long call strike, the loss is capped
    # ..."), which reads well and cannot be compared to anything. Optional
    # because every idea briefed before 3.7 has none, and a review that
    # invented levels for those would be worse than one that says so.
    stop_level: Price | None = None
    target_level: Price | None = None
    expiry: date | None = None
    # The structure as numbers, added in step 3.8 so it can be scored. Empty for
    # every idea briefed before migration 0009; an idea with no legs is reported
    # as unscoreable rather than scored on a guessed structure.
    legs: tuple[RecordedLeg, ...] = ()


@dataclass(frozen=True)
class RecordedDecision:
    """What the user did about an idea, and why.

    `reason` is free text on purpose. It is the input to working out which rule
    the user is applying that the system does not yet know about.
    """

    idea_id: UUID
    action: str  # TOOK | SKIPPED | MODIFIED
    reason: str
    decided_at: datetime
    # Both None unless the idea was taken. Size is the user's own number: an LLM
    # never sizes a position (rule 1). It is recorded after the fact so outcomes
    # can be scored, never to drive an order.
    entry_fill: Money | None = None
    size: int | None = None

    def __post_init__(self) -> None:
        if not self.reason.strip():
            raise ValueError(f"{self.idea_id}: a decision must carry a reason")


@dataclass(frozen=True)
class RecordedOutcome:
    """How an idea resolved. Recorded for skipped ideas too.

    Scoring only the taken ideas would measure the user's taste rather than the
    system's research, and would never reveal that the skips were the good calls.
    """

    idea_id: UUID
    resolution: str  # TARGET | STOP | TIME_STOP | INVALIDATED
    hypothetical_pnl: Money
    actual_pnl: Money | None  # None when the user did not take it
    resolved_at: datetime
    # What the resolution was measured against - SNAPSHOT, EXPIRY or
    # INVALIDATION. Recorded because the archive holds three chain snapshots a
    # day, so a stop is detected at 09:30, 12:30 or 15:15 and not at the moment
    # it was breached. A backtest that silently assumes an intraday fill
    # flatters itself, and the P&L alone cannot tell you which happened.
    basis: str = "SNAPSHOT"
    # Gross and costs kept beside the net figure. The difference between them is
    # the whole point of FR-7.6 - for a vertical it is ~13% of the best case
    # (ISS-023), and a net number alone cannot answer whether the strategy
    # survives its own costs.
    gross_pnl: Money | None = None
    costs: Money | None = None
    # What fired the resolution, and the threshold it was compared against
    # (rule 9), so "why was this a stop?" is answerable from the row.
    measured: str = ""
    threshold: str = ""
    # How far it went against you before it worked. A winner that first drew
    # down three times the stop is not a repeatable idea.
    max_adverse_excursion: Money | None = None


@dataclass(frozen=True)
class RecordedOverride:
    """A disagreement between the user and the system.

    `what_followed` is optional because the disagreement is worth recording
    before the result is known. Waiting for the result is how override logs end
    up empty.
    """

    idea_id: UUID
    system_position: str
    user_action: str
    reason: str
    recorded_at: datetime
    what_followed: str | None = None


@dataclass(frozen=True)
class ResolvedScore:
    """One resolved idea with the confidence that was claimed for it.

    A join, given its own type because the calibration maths needs exactly
    these fields and nothing else. Fetching outcomes and then looking up each
    idea's confidence one at a time would be the same query run fifty times.

    `used_iv_proxy` travels with it on purpose. Until roughly six months of
    chain history accrues, most ideas' IV rank comes from the realised-vol
    proxy, and a calibration that mixed those with real-IV ideas would be
    measuring two different systems at once. The flag is what lets the two be
    separated later, which is the reason step 1.8 recorded it at all.
    """

    idea_id: UUID
    setup_id: str
    confidence: float
    resolution: str
    net_pnl: Money
    resolved_at: datetime
    used_iv_proxy: bool = False


@dataclass(frozen=True)
class CalibrationSnapshot:
    """Whether the confidence numbers mean anything, measured not assumed."""

    as_of: datetime
    scope: str  # an agent name, a setup id, or ALL
    brier_score: float
    # Always predicting the base rate. Without it a Brier score is a number
    # with no sense of whether the model beat guessing.
    baseline_brier_score: float
    sample_count: int
    reliability_bins: dict[str, str] = field(default_factory=dict)


class JournalPort(Protocol):
    def record_idea(self, idea: RecordedIdea) -> None:
        """Append one briefed idea. The same idea_id twice is an error."""
        ...

    def record_decision(self, decision: RecordedDecision) -> None:
        """Append a decision. A second one for the same idea is a correction."""
        ...

    def record_outcome(self, outcome: RecordedOutcome) -> None:
        """Append an outcome. A second one for the same idea is a correction."""
        ...

    def record_override(self, override: RecordedOverride) -> None: ...

    def record_calibration(self, snapshot: CalibrationSnapshot) -> None: ...

    def ideas_awaiting_decision(self, as_of: datetime) -> list[RecordedIdea]:
        """Ideas with no decision yet. Drives the 10:00 nag."""
        ...

    def ideas_without_outcome(self, since: date, limit: int) -> list[RecordedIdea]:
        """Briefed ideas that have not resolved yet, oldest first.

        Deliberately not the same question as `ideas_awaiting_decision`. That
        one asks who owes an answer; this one asks what is still running. A
        taken idea has a decision and is exactly the one the 15:45 review most
        needs to look at, so reusing the nag's query would review everything
        except the trades.
        """
        ...

    def latest_decision(self, idea_id: UUID) -> RecordedDecision | None:
        """The current decision for an idea - the newest row, corrections included."""
        ...

    def resolved_ideas(self, limit: int) -> list[RecordedOutcome]:
        """Most recent resolved outcomes, for the calibration tracker."""
        ...

    def briefed_dates(self, since: date) -> list[date]:
        """IST dates on which at least one idea was briefed, oldest first.

        The no-trade monitor needs "did this session produce anything", and the
        answer is a date list rather than a count because two ideas on one day
        is still one session that produced something.
        """
        ...

    def engagement_counts(self, since: date) -> tuple[int, int]:
        """(ideas briefed, ideas with a decision) since a date.

        Two integers rather than a dataclass because they are only ever used
        together, by one monitor, to compute one fraction. A SKIPPED decision
        counts as answered: the user read the idea and rejected it, which is
        the system working.
        """
        ...

    def resolved_scores(self, limit: int) -> list[ResolvedScore]:
        """Resolved outcomes joined to the confidence that was claimed, newest first.

        Separate from `resolved_ideas` because that one answers "what happened"
        and this one answers "was the confidence any good" - and the second
        question needs a column the first does not return.
        """
        ...
