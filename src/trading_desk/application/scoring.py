"""The 16:00 cycle: resolve what finished today, and score it after costs.

It runs after the 15:45 review on purpose. The review says where every open
idea stands and records nothing; this says which ones are **over** and writes
one `outcomes` row each. Splitting them keeps the message you read at 15:45
free of arithmetic you cannot check, and keeps the row that goes in the journal
out of a message that is written to be skimmed.

**Three ways an idea resolves, and they are not interchangeable:**

- the spread's premium reached the setup's target or stop (`SNAPSHOT`),
- the time stop landed (`SNAPSHOT`, still - it is read off a snapshot),
- the expiry passed (`EXPIRY`).

Thesis invalidation (`INVALIDATION`) is the fourth in the schema and is not
produced here. Deciding that a thesis is broken is a judgement about news, not
a comparison of two numbers, and inventing it from price action would put a
label in the journal that nobody made. It stays for when the user or the news
agent supplies one.

**What this refuses to do**, because `outcomes` is append-only and a wrong row
is permanent:

- score an idea with no recorded legs (everything briefed before migration 0009),
- score an idea whose exit premium is not in the chain archive,
- score a single-leg idea against a fraction-of-width target, which has no width.

Each refusal is reported with its reason and writes nothing (rule 6). A day on
which nothing could be scored produces a report saying so, not silence.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from trading_desk.domain.costs import CostConfig
from trading_desk.domain.enums import SetupId
from trading_desk.domain.scoring import (
    STRIKES_IN_A_SPREAD,
    CreditExitRules,
    DebitExitRules,
    ExitTest,
    Resolution,
    ResolutionBasis,
    Score,
    ScoredLeg,
    SpreadState,
    credit_exit,
    debit_exit,
    score,
    spread_state,
)
from trading_desk.domain.setups.thresholds import SetupThresholds
from trading_desk.domain.timezones import to_ist
from trading_desk.domain.values import Money
from trading_desk.ports.chain_archive import ChainArchivePort
from trading_desk.ports.clock import Clock
from trading_desk.ports.journal import JournalPort, RecordedIdea, RecordedLeg, RecordedOutcome
from trading_desk.ports.notifier import DeliveryReceipt, NotifierPort

log = logging.getLogger(__name__)

# How far back to look for unresolved ideas. Matches the review's window: an
# idea older than this with no outcome is a gap to investigate by hand, not
# something to score against a three-month-old premium.
OPEN_IDEA_LOOKBACK_DAYS = 90
MAX_OPEN_IDEAS = 50

# The hypothetical size every score is computed at. **One lot's worth of
# contracts is not a position size** - it is the unit the whole journal already
# uses for `hypothetical_risk` (ISS-022: there is no capital figure, so there
# is no position). Scoring every idea at the same quantity is also what makes
# two ideas comparable at all.
SCORING_QUANTITY = 1


@dataclass(frozen=True)
class Unscoreable:
    """An idea that finished, or might have, and could not be scored."""

    symbol: str
    briefed_on: date
    reason: str

    def as_line(self) -> str:
        return f"{self.symbol} briefed {self.briefed_on:%d %b}: {self.reason}"


@dataclass(frozen=True)
class ScoredIdea:
    """An idea that resolved, with the row that was written for it."""

    symbol: str
    setup_id: str
    briefed_on: date
    result: Score

    def as_line(self) -> str:
        return (
            f"{self.symbol} ({self.setup_id}) briefed "
            f"{self.briefed_on:%d %b}: {self.result.as_line()}"
        )


@dataclass(frozen=True)
class ScoringReport:
    """What one run of the cycle did."""

    as_of: datetime
    scored: tuple[ScoredIdea, ...]
    still_open: int
    unscoreable: tuple[Unscoreable, ...]

    @property
    def net_total(self) -> Money:
        return Money(paise=sum(item.result.net.paise for item in self.scored))

    def text(self) -> str:
        when = to_ist(self.as_of)
        lines = [f"SCORING {when:%a %d %b %Y %H:%M} IST", ""]

        if not self.scored and not self.unscoreable:
            lines.append(f"Nothing resolved today. {self.still_open} idea(s) still open.")
            return "\n".join(lines)

        lines.append(
            f"{len(self.scored)} resolved and scored, {self.still_open} still open, "
            f"{len(self.unscoreable)} could not be scored"
        )
        if self.scored:
            lines.append("")
            lines.extend(item.as_line() for item in self.scored)
            lines.append("")
            lines.append(f"Net across the above, after full costs: {_rupees(self.net_total)}")

        if self.unscoreable:
            lines.append("")
            lines.append("Not scored:")
            lines.extend(f"  {item.as_line()}" for item in self.unscoreable)

        lines.append("")
        lines.append(
            "Hypothetical, at one contract each, measured from the archive's last "
            "snapshot of the day - not from an intraday fill."
        )
        return "\n".join(lines)


class ScoringCycle:
    """Resolves and scores open ideas. Writes one outcome row per resolution."""

    def __init__(
        self,
        *,
        journal: JournalPort,
        chains: ChainArchivePort,
        clock: Clock,
        thresholds: SetupThresholds,
        costs: CostConfig,
        lookback_days: int = OPEN_IDEA_LOOKBACK_DAYS,
    ) -> None:
        self._journal = journal
        self._chains = chains
        self._clock = clock
        self._thresholds = thresholds
        self._costs = costs
        self._lookback = lookback_days

    def run(self) -> ScoringReport:
        now = self._clock.utc_now()
        today = to_ist(now).date()
        since = today - timedelta(days=self._lookback)

        scored: list[ScoredIdea] = []
        unscoreable: list[Unscoreable] = []
        still_open = 0

        for idea in self._journal.ideas_without_outcome(since, MAX_OPEN_IDEAS):
            result = self._score_one(idea, today)
            if isinstance(result, Unscoreable):
                unscoreable.append(result)
                continue
            if result is None:
                still_open += 1
                continue

            self._journal.record_outcome(
                RecordedOutcome(
                    idea_id=idea.idea_id,
                    resolution=str(result.result.resolution),
                    hypothetical_pnl=result.result.net,
                    # None, not zero. Zero would claim the user took the trade
                    # and broke even. Step 3.6 records a fill when one exists;
                    # nothing here knows whether it did.
                    actual_pnl=None,
                    resolved_at=now,
                    basis=str(result.result.basis),
                    gross_pnl=result.result.gross,
                    costs=result.result.costs,
                    measured=result.result.measured,
                    threshold=result.result.threshold,
                )
            )
            scored.append(result)

        return ScoringReport(
            as_of=now,
            scored=tuple(scored),
            still_open=still_open,
            unscoreable=tuple(unscoreable),
        )

    def _why_not_scoreable(self, idea: RecordedIdea, today: date) -> str | None:
        """The reason this idea cannot be scored, or None if it can be.

        Separate from the scoring itself because these are four different
        answers to "why is there no number", and each one calls for a different
        response: wait, backfill the archive, or go and look at the row.
        """
        if not idea.legs:
            return "no legs recorded (briefed before migration 0009)"
        if idea.expiry is None:
            return "no expiry recorded"
        if self._exit_rules(idea) is None:
            return f"no exit rules for setup {idea.setup_id}"
        if len(idea.legs) < STRIKES_IN_A_SPREAD:
            return f"{len(idea.legs)} leg(s): a fraction-of-width target needs a width"
        if self._exit_legs(idea, today) is None:
            return f"no archived premium for every leg on {today:%d %b}"
        return None

    def _score_one(self, idea: RecordedIdea, today: date) -> ScoredIdea | Unscoreable | None:
        """A score, a stated refusal, or None for "still running"."""
        refusal = self._why_not_scoreable(idea, today)
        if refusal is not None:
            return Unscoreable(idea.symbol, idea.created_at.date(), refusal)

        # Every one of these is non-None: `_why_not_scoreable` just checked.
        exits = self._exit_rules(idea)
        priced = self._exit_legs(idea, today)
        if exits is None or priced is None or idea.expiry is None:
            raise RuntimeError(f"{idea.symbol}: scoreable idea is missing its inputs")

        strikes = tuple(leg.strike for leg in idea.legs)
        state = spread_state(priced, strikes)
        days_left = (idea.expiry - today).days

        # Expiry first, before the premium rulebook, the same ordering
        # `domain/review.py` uses. A contract four days past expiry does not
        # exist any more, and letting the time stop claim it would record
        # "flat with five days left" as the reason - which is a different event
        # and would make the time-stop count meaningless.
        if days_left < 0:
            test = ExitTest(
                Resolution.TIME_STOP,
                f"{abs(days_left)} day(s) past the {idea.expiry:%d %b} expiry",
                "expiry passed",
            )
            basis = ResolutionBasis.EXPIRY
        else:
            test = _test_exit(state, exits, days_to_expiry=days_left)
            basis = ResolutionBasis.SNAPSHOT

        resolution = test.resolution
        if resolution is None:
            return None

        return ScoredIdea(
            symbol=idea.symbol,
            setup_id=idea.setup_id,
            briefed_on=idea.created_at.date(),
            result=score(
                legs=priced,
                strikes=strikes,
                resolution=resolution,
                basis=basis,
                exit_test=test,
                quantity=SCORING_QUANTITY,
                rates=self._costs.rates,
            ),
        )

    def _exit_rules(self, idea: RecordedIdea) -> DebitExitRules | CreditExitRules | None:
        """Which rulebook applies. Setup B's two variants are not the same rules.

        Setup B's variant is decided by whether the structure was put on for a
        credit or a debit, which is a fact about the recorded legs rather than
        something stored as a label. Reading it from the legs means a mislabelled
        idea is still scored by the arithmetic that actually applies to it.
        """
        if idea.setup_id == SetupId.CATALYST_CONTINUATION.value:
            return self._thresholds.setup_a.exits
        if idea.setup_id == SetupId.INDEX_VOL_REGIME.value:
            paid = sum(
                leg.entry_premium.paise * (1 if leg.action.value == "BUY" else -1)
                for leg in idea.legs
            )
            return (
                self._thresholds.setup_b.debit_exits
                if paid > 0
                else self._thresholds.setup_b.credit_exits
            )
        return None

    def _exit_legs(self, idea: RecordedIdea, today: date) -> tuple[ScoredLeg, ...] | None:
        """Each leg's entry premium paired with today's. None if any is missing.

        All legs or none, for the same reason the entry premiums are recorded
        that way: a vertical priced on one leg is a different trade.
        """
        priced = []
        for leg in idea.legs:
            now = self._chains.premium_on(
                underlying=idea.symbol,
                on_date=today,
                expiry=leg.expiry,
                strike=leg.strike,
                right=leg.right,
            )
            if now is None:
                log.info(
                    "%s: no archived premium for the %s %s on %s",
                    idea.symbol,
                    leg.strike.rupees(),
                    leg.right.value,
                    today.isoformat(),
                )
                return None
            priced.append(_as_scored_leg(leg, now))
        return tuple(priced)


def _test_exit(
    state: SpreadState, exits: DebitExitRules | CreditExitRules, *, days_to_expiry: int
) -> ExitTest:
    """Dispatch to the right rulebook. Two functions, never one with a flag."""
    if isinstance(exits, CreditExitRules):
        return credit_exit(state, exits)
    return debit_exit(state, exits, days_to_expiry=days_to_expiry)


def _as_scored_leg(leg: RecordedLeg, exit_premium: Money) -> ScoredLeg:
    return ScoredLeg(
        action=leg.action,
        entry_premium=leg.entry_premium,
        exit_premium=exit_premium,
    )


def _rupees(amount: Money) -> str:
    sign = "-" if amount.paise < 0 else ""
    return f"{sign}Rs {abs(amount.paise) / 100:,.2f}"


def deliver(report: ScoringReport, notifier: NotifierPort) -> DeliveryReceipt:
    """Send the report. Raises if it could not be delivered (rule 6).

    `send_briefing`, not `send_alert`: this is something to read with the
    evening's review, not something that should wake anyone. A failure to score
    is already reported inside the text.
    """
    return notifier.send_briefing(report.text())
