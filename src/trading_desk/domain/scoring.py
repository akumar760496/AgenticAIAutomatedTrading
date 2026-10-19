"""How an idea ended, and what it would have earned after costs.

**The exit rules here are on the spread's premium, not on the underlying.**
`docs/06` §2 says Setup A exits at "60% of spread width" and stops at "50% of
the debit lost". Step 3.7's review compares the underlying's close to a stop and
target level, which answers *where is this idea now* - a useful thing to put in
a 15:45 message and a different question from whether the trade is over. Scoring
the underlying's levels would score a strategy nobody wrote down.

Two consequences worth stating before reading the code:

- **A credit spread and a debit spread are scored by opposite arithmetic.** A
  debit trade's gain is capped by the spread width; a credit trade's gain is
  capped by the credit received, and its loss by the width minus that credit.
  Collapsing them into one formula is how a credit spread quietly gets scored
  as a debit one, so they are separate functions with separate tests.
- **Costs are charged on the real exit premium.** `round_trip_cost` models the
  exit at the entry premium and its docstring says the caller must recompute
  when a real exit price is known. This is that caller.

Everything here is a pure function over plain values. The premiums arrive as
arguments; finding them in the archive is `application/scoring.py`'s job.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from trading_desk.domain.costs import CostRates, LegCost, TradeCost, leg_cost
from trading_desk.domain.proposals import LegAction
from trading_desk.domain.values import Money, Price

# A vertical. Fewer strikes than this has no width, and width is what caps the
# payoff at both ends.
STRIKES_IN_A_SPREAD = 2


class Resolution(StrEnum):
    """How an idea ended. The four values the `outcomes` table allows."""

    TARGET = "TARGET"
    STOP = "STOP"
    TIME_STOP = "TIME_STOP"
    INVALIDATED = "INVALIDATED"


class ResolutionBasis(StrEnum):
    """What the resolution was measured against.

    Recorded on every outcome rather than assumed, because the three are not
    equally trustworthy and a reader six months from now cannot tell them apart
    from the P&L alone. `SNAPSHOT` is the honest default: the archive holds
    three chain snapshots a day, so a stop is detected at 09:30, 12:30 or 15:15
    and not at the moment it was breached.
    """

    SNAPSHOT = "SNAPSHOT"
    EXPIRY = "EXPIRY"
    INVALIDATION = "INVALIDATION"


@dataclass(frozen=True)
class DebitExitRules:
    """Setup A and Setup B's debit variant. Fractions, from config."""

    target_fraction_of_width: Decimal
    stop_fraction_of_debit_lost: Decimal
    time_stop_days_to_expiry: int

    def __post_init__(self) -> None:
        _require_fraction("target_fraction_of_width", self.target_fraction_of_width)
        _require_fraction("stop_fraction_of_debit_lost", self.stop_fraction_of_debit_lost)
        if self.time_stop_days_to_expiry < 0:
            raise ValueError(
                f"time_stop_days_to_expiry must not be negative, "
                f"got {self.time_stop_days_to_expiry}"
            )


@dataclass(frozen=True)
class CreditExitRules:
    """Setup B's credit variant. The stop is a multiple, not a fraction."""

    target_fraction_of_credit_captured: Decimal
    stop_multiple_of_credit: Decimal

    def __post_init__(self) -> None:
        _require_fraction(
            "target_fraction_of_credit_captured", self.target_fraction_of_credit_captured
        )
        if self.stop_multiple_of_credit <= 0:
            raise ValueError(
                f"stop_multiple_of_credit must be positive, got {self.stop_multiple_of_credit}"
            )


def _require_fraction(name: str, value: Decimal) -> None:
    if not Decimal(0) < value <= Decimal(1):
        raise ValueError(f"{name} must be over 0 and at most 1, got {value}")


@dataclass(frozen=True)
class ScoredLeg:
    """One leg, with the premium observed at entry and at exit.

    Both premiums are **observations** - what the chain printed - not prices the
    system chose. A proposal still carries no price of any kind (rule 1); this
    is the journal's record of what the market was doing, which is the only way
    a P&L can be computed at all.
    """

    action: LegAction
    entry_premium: Money
    exit_premium: Money

    def __post_init__(self) -> None:
        if self.entry_premium.paise < 0 or self.exit_premium.paise < 0:
            raise ValueError("a premium cannot be negative")


@dataclass(frozen=True)
class SpreadState:
    """What a structure is worth now, against what it cost.

    `net_entry` is positive for a debit (you paid) and negative for a credit
    (you received). The sign is the single thing that decides which rulebook
    applies, so it is computed once here rather than passed in as a flag.
    """

    net_entry: Money
    net_now: Money
    width: Money

    @property
    def is_debit(self) -> bool:
        return self.net_entry.paise > 0

    @property
    def open_profit(self) -> Money:
        """Gross move, before costs. Positive is good, whichever way it was put on."""
        return Money(paise=self.net_now.paise - self.net_entry.paise)


def spread_state(legs: tuple[ScoredLeg, ...], strikes: tuple[Price, ...]) -> SpreadState:
    """The net paid, the net now, and the width. Signs follow the actions.

    A bought leg is money out and a sold leg is money in, so the net is the
    buys minus the sells at both ends. Width is the distance between the
    outermost strikes, which is what caps a vertical either way.
    """
    if not legs:
        raise ValueError("a structure needs at least one leg")
    if len(strikes) < STRIKES_IN_A_SPREAD:
        # A single-leg idea has no width to cap anything, and the fraction-of-
        # width target is meaningless for it. Refused rather than given a width
        # of zero, which would make every target fire immediately.
        raise ValueError(
            f"a spread needs {STRIKES_IN_A_SPREAD} strikes to have a width, got {len(strikes)}"
        )

    net_entry = 0
    net_now = 0
    for leg in legs:
        sign = 1 if leg.action is LegAction.BUY else -1
        net_entry += sign * leg.entry_premium.paise
        net_now += sign * leg.exit_premium.paise

    return SpreadState(
        net_entry=Money(paise=net_entry),
        net_now=Money(paise=net_now),
        width=Money(paise=max(s.paise for s in strikes) - min(s.paise for s in strikes)),
    )


@dataclass(frozen=True)
class ExitTest:
    """Whether an exit rule has been met, and the two numbers it compared.

    Rule 9: a gate reports what it measured and the threshold, so "why did this
    resolve as a stop?" is answerable from the record rather than by rerunning
    the code against data that has since changed.
    """

    resolution: Resolution | None
    measured: str
    threshold: str

    @property
    def fired(self) -> bool:
        return self.resolution is not None


def debit_exit(state: SpreadState, rules: DebitExitRules, *, days_to_expiry: int) -> ExitTest:
    """Has a debit spread hit its target, its stop, or its time stop?

    Order matters and is deliberate: target, then stop, then time. A spread sat
    at its target on the day its time stop lands resolved by reaching the
    target, and calling that a time stop would under-report the setup's hit rate
    for ever.
    """
    paid = state.net_entry.paise
    if paid <= 0:
        raise ValueError(f"a debit spread must have cost something, net entry {paid}p")

    gained = state.net_now.paise - paid
    target = int(Decimal(state.width.paise) * rules.target_fraction_of_width)
    if gained >= target:
        return ExitTest(
            Resolution.TARGET,
            f"{_rupees(gained)} gained on a {_rupees(state.width.paise)} width",
            f">= {_rupees(target)} ({rules.target_fraction_of_width:%} of width)",
        )

    lost = paid - state.net_now.paise
    stop = int(Decimal(paid) * rules.stop_fraction_of_debit_lost)
    if lost >= stop:
        return ExitTest(
            Resolution.STOP,
            f"{_rupees(lost)} of a {_rupees(paid)} debit lost",
            f">= {_rupees(stop)} ({rules.stop_fraction_of_debit_lost:%} of debit)",
        )

    if days_to_expiry <= rules.time_stop_days_to_expiry:
        return ExitTest(
            Resolution.TIME_STOP,
            f"{days_to_expiry} day(s) to expiry, {_rupees(gained)} open",
            f"<= {rules.time_stop_days_to_expiry} days to expiry",
        )

    return ExitTest(
        None,
        f"{_rupees(gained)} open, {days_to_expiry} day(s) to expiry",
        f"target {_rupees(target)}, stop {_rupees(stop)}",
    )


def credit_exit(state: SpreadState, rules: CreditExitRules) -> ExitTest:
    """Has a credit spread captured enough, or lost a multiple of the credit?

    There is no time stop here. Setup B's credit variant is flat the session
    before expiry by `flat_by_sessions_before_expiry`, which is a scheduling
    rule the review enforces, not an outcome. Inventing a time stop for it
    would resolve trades the rulebook says are still open.
    """
    received = -state.net_entry.paise
    if received <= 0:
        raise ValueError(f"a credit spread must have paid you something, net entry {received}p")

    # Still owed: what it would cost to close now. Captured is the difference.
    owed = -state.net_now.paise
    captured = received - owed
    target = int(Decimal(received) * rules.target_fraction_of_credit_captured)
    if captured >= target:
        return ExitTest(
            Resolution.TARGET,
            f"{_rupees(captured)} of a {_rupees(received)} credit captured",
            f">= {_rupees(target)} ({rules.target_fraction_of_credit_captured:%} of credit)",
        )

    stop = int(Decimal(received) * rules.stop_multiple_of_credit)
    if owed >= stop:
        return ExitTest(
            Resolution.STOP,
            f"{_rupees(owed)} to close against {_rupees(received)} received",
            f">= {_rupees(stop)} ({rules.stop_multiple_of_credit}x credit)",
        )

    return ExitTest(
        None,
        f"{_rupees(captured)} captured, {_rupees(owed)} to close",
        f"target {_rupees(target)}, stop {_rupees(stop)}",
    )


@dataclass(frozen=True)
class Score:
    """One idea's hypothetical result, with the arithmetic kept visible.

    `gross` and `costs` are both reported because the difference between them
    is the whole point of FR-7.6. A net figure alone cannot answer "would this
    strategy survive its own costs?", which for a vertical is roughly a 13%
    question (ISS-023).
    """

    resolution: Resolution
    basis: ResolutionBasis
    gross: Money
    costs: Money
    quantity: int
    measured: str
    threshold: str

    @property
    def net(self) -> Money:
        return Money(paise=self.gross.paise - self.costs.paise)

    def as_line(self) -> str:
        return (
            f"{self.resolution}: gross {_rupees(self.gross.paise)}, "
            f"costs {_rupees(self.costs.paise)}, net {_rupees(self.net.paise)} "
            f"({self.quantity} contracts, {self.basis.lower()}) - "
            f"{self.measured} vs {self.threshold}"
        )


def round_trip_cost_at_exit(
    legs: tuple[ScoredLeg, ...], quantity: int, rates: CostRates
) -> TradeCost:
    """Entry costs at the entry premium, exit costs at the exit premium.

    This is the recomputation `domain/costs.py` asks the caller to do. Charging
    the exit at the entry premium is wrong in a predictable direction: a winning
    debit spread exits at a *higher* premium, so its STT and exchange charges
    are larger than the entry's, and reusing the entry premium understates the
    cost of exactly the trades that look best.
    """
    costs: list[LegCost] = []
    for leg in legs:
        opposite = LegAction.SELL if leg.action is LegAction.BUY else LegAction.BUY
        costs.append(leg_cost(leg.entry_premium, quantity, leg.action, rates))
        costs.append(leg_cost(leg.exit_premium, quantity, opposite, rates))
    return TradeCost(legs=tuple(costs))


def score(
    *,
    legs: tuple[ScoredLeg, ...],
    strikes: tuple[Price, ...],
    resolution: Resolution,
    basis: ResolutionBasis,
    exit_test: ExitTest,
    quantity: int,
    rates: CostRates,
) -> Score:
    """Hypothetical P&L for a resolved idea, after full costs.

    `quantity` is an argument and lives nowhere else, exactly as in
    `domain/costs.py`: this computes a number for a hypothetical size, it does
    not choose one (rule 1).
    """
    if quantity <= 0:
        raise ValueError(f"quantity must be positive, got {quantity}")

    state = spread_state(legs, strikes)
    gross = Money(paise=state.open_profit.paise * quantity)
    return Score(
        resolution=resolution,
        basis=basis,
        gross=gross,
        costs=round_trip_cost_at_exit(legs, quantity, rates).total,
        quantity=quantity,
        measured=exit_test.measured,
        threshold=exit_test.threshold,
    )


def _rupees(paise: int) -> str:
    """Paise as rupees for a human. Negative sign in front of the symbol."""
    sign = "-" if paise < 0 else ""
    return f"{sign}Rs {abs(paise) / 100:,.2f}"
