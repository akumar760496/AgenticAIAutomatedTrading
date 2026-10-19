"""The 15:45 job: where every open idea stands at the close.

**It reports and it does not record.** `docs/10` gives the outcome-resolution
rules and hypothetical P&L after full costs to step 3.8, and `outcomes` is
append-only - a placeholder P&L written today would be permanent and would be
indistinguishable from a measured one later, in the one dataset this whole
project exists to build. So an idea that has run past its target is listed as
*ready to resolve* and the row is left unwritten.

That makes this job's value the part that is easy to overlook: it is a daily,
dated statement that the system is still watching the ideas it gave you, and it
is where "the 08:15 job has been failing for a week" becomes visible without
anyone going to look.

Two things it deliberately does not do:

- **It does not fetch prices.** It reads the bar store, which the 15:15 capture
  fills. If today's bar is not there, the standing is "no daily bar for today"
  rather than a number fetched from somewhere else at 15:45, because a review
  that quietly used a different price source than the research did would be
  comparing two things.
- **It does not judge the thesis.** Each idea's invalidation sentence is quoted
  back verbatim for the reader to judge. Asking a model whether its own thesis
  had been invalidated is a graded exam with no invigilator.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from trading_desk.domain.enums import Direction
from trading_desk.domain.review import IdeaStanding, Standing, standing_for
from trading_desk.domain.timezones import to_ist
from trading_desk.domain.values import Price
from trading_desk.ports.bar_store import BarStorePort
from trading_desk.ports.clock import Clock
from trading_desk.ports.journal import JournalPort, RecordedIdea
from trading_desk.ports.notifier import DeliveryReceipt, NotifierPort

# How far back open ideas are looked for. A v1 idea expires within about five
# weeks, so ninety days covers every live one and anything older than that is
# a bug in resolution rather than an open position.
OPEN_IDEA_LOOKBACK_DAYS = 90

MAX_OPEN_IDEAS = 50

NO_DECISION = "no decision recorded"

# Standings that 3.8 will turn into an `outcomes` row. Named here so the
# review can count them and the report can say what is waiting.
READY_TO_RESOLVE = (IdeaStanding.PAST_TARGET, IdeaStanding.PAST_STOP, IdeaStanding.EXPIRED)


@dataclass(frozen=True)
class IdeaReview:
    """One open idea at the close."""

    symbol: str
    direction: Direction
    setup_id: str
    briefed_on: date
    standing: Standing
    decision: str
    invalidation: str

    @property
    def is_ready_to_resolve(self) -> bool:
        return self.standing.standing in READY_TO_RESOLVE

    def as_lines(self) -> list[str]:
        return [
            f"{self.symbol} {self.direction} ({self.setup_id}) briefed {self.briefed_on:%d %b}",
            f"  {self.standing.standing}: {self.standing.detail}",
            f"  your decision: {self.decision}",
            f"  wrong if: {self.invalidation}",
        ]


@dataclass(frozen=True)
class DayReview:
    """The post-close report."""

    as_of: datetime
    reviews: tuple[IdeaReview, ...]

    @property
    def ready_to_resolve(self) -> int:
        return sum(1 for review in self.reviews if review.is_ready_to_resolve)

    @property
    def undecided(self) -> int:
        return sum(1 for review in self.reviews if review.decision == NO_DECISION)

    @property
    def unknown(self) -> int:
        return sum(1 for review in self.reviews if not review.standing.is_known)

    def text(self) -> str:
        ist = to_ist(self.as_of)
        lines = [f"POST-CLOSE REVIEW {ist:%a %d %b %Y %H:%M} IST", ""]

        if not self.reviews:
            lines.append("No open ideas. Nothing to review.")
            return "\n".join(lines)

        lines.append(
            f"{len(self.reviews)} open idea(s): {self.ready_to_resolve} ready to resolve, "
            f"{self.undecided} with no decision, {self.unknown} that cannot be told"
        )
        lines.append("")
        for review in self.reviews:
            lines.extend(review.as_lines())
            lines.append("")

        # Said every day, because the alternative is a reader who takes
        # "ready to resolve" for a measured result. Step 3.8 made this more
        # important rather than less: scoring now runs at 16:00, so the two
        # messages arrive fifteen minutes apart and must not be confused.
        lines.append(
            "'Ready to resolve' means the underlying has reached a level on the chart - "
            "not that a P&L has been measured. The 16:00 scoring run does that, against "
            "the spread's own premium, and only it writes an outcome."
        )
        return "\n".join(lines)


class ReviewCycle:
    def __init__(
        self,
        *,
        journal: JournalPort,
        bars: BarStorePort,
        clock: Clock,
        lookback_days: int = OPEN_IDEA_LOOKBACK_DAYS,
    ) -> None:
        self._journal = journal
        self._bars = bars
        self._clock = clock
        self._lookback_days = lookback_days

    def run(self) -> DayReview:
        now = self._clock.utc_now()
        today = to_ist(now).date()
        since = today - timedelta(days=self._lookback_days)

        reviews = [
            self._review_one(idea, today)
            for idea in self._journal.ideas_without_outcome(since, MAX_OPEN_IDEAS)
        ]
        return DayReview(as_of=now, reviews=tuple(reviews))

    def _review_one(self, idea: RecordedIdea, today: date) -> IdeaReview:
        decision = self._journal.latest_decision(idea.idea_id)
        return IdeaReview(
            symbol=idea.symbol,
            direction=idea.direction,
            setup_id=idea.setup_id,
            briefed_on=to_ist(idea.created_at).date(),
            standing=standing_for(
                direction=idea.direction,
                close=self._close_today(idea.symbol, today),
                stop=idea.stop_level,
                target=idea.target_level,
                expiry=idea.expiry,
                today=today,
            ),
            decision=decision.action if decision is not None else NO_DECISION,
            invalidation=idea.invalidation or "not recorded",
        )

    def _close_today(self, symbol: str, today: date) -> Price | None:
        """Today's close, or None if today's bar is not in the store.

        The date is checked rather than trusted. `daily_bars` returns the most
        recent bar whatever its date, so a feed that stopped on Tuesday would
        otherwise have Tuesday's close reported as Friday's, every day, and the
        report would look healthy while being three days stale (rule 6).
        """
        bars = self._bars.daily_bars(symbol, limit=1)
        if not bars:
            return None

        latest = bars[-1]
        if to_ist(latest.start).date() != today:
            return None
        return latest.close


def deliver(review: DayReview, notifier: NotifierPort) -> DeliveryReceipt:
    """Send the review. Raises if it could not be delivered (rule 6)."""
    return notifier.send_briefing(review.text())
