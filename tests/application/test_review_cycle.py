"""The 15:45 review.

The two tests worth the most are the ones about what it refuses to say: a
stale bar store must not produce a confident standing, and a taken idea must
not be left out of the review because it already has a decision.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import uuid4

from tests.fakes import FakeBarStore, FakeJournal, FakeNotifier, FrozenClock
from trading_desk.application.review_cycle import NO_DECISION, ReviewCycle, deliver
from trading_desk.domain.enums import Direction, Exchange, Segment
from trading_desk.domain.instruments import InstrumentRef
from trading_desk.domain.market import Bar
from trading_desk.domain.review import IdeaStanding
from trading_desk.domain.values import Money, Price
from trading_desk.ports.journal import RecordedDecision, RecordedIdea, RecordedOutcome

# 15:45 IST on Monday 19 October 2026.
CLOSE_TIME = datetime(2026, 10, 19, 10, 15, tzinfo=UTC)
TODAY = date(2026, 10, 19)
BRIEFED_AT = datetime(2026, 10, 16, 2, 45, tzinfo=UTC)


def _idea(
    *,
    symbol: str = "RELIANCE",
    direction: Direction = Direction.BULLISH,
    stop: str | None = "1400",
    target: str | None = "1440",
    expiry: date | None = date(2026, 11, 5),
) -> RecordedIdea:
    return RecordedIdea(
        idea_id=uuid4(),
        cycle_id=uuid4(),
        symbol=symbol,
        setup_id="SETUP_A",
        direction=direction,
        rank=1,
        confidence=0.6,
        gate_values={"iv_rank": "40.0 (proxy)"},
        hypothetical_risk=Money.from_rupees(5500),
        created_at=BRIEFED_AT,
        invalidation="the stock closes back inside its prior range",
        stop_level=None if stop is None else Price.from_rupees(stop),
        target_level=None if target is None else Price.from_rupees(target),
        expiry=expiry,
    )


def _bar(symbol: str, day: date, close: str) -> Bar:
    level = Price.from_rupees(close)
    return Bar(
        instrument=InstrumentRef(
            symbol=symbol,
            exchange=Exchange.NSE,
            segment=Segment.EQUITY,
            security_id="2885",
            lot_size=250,
        ),
        # 09:15 IST on that date.
        start=datetime(day.year, day.month, day.day, 3, 45, tzinfo=UTC),
        open=level,
        high=level,
        low=level,
        close=level,
        volume=1_000_000,
    )


def _cycle(journal: FakeJournal, bars: FakeBarStore, moment: datetime = CLOSE_TIME) -> ReviewCycle:
    return ReviewCycle(journal=journal, bars=bars, clock=FrozenClock(moment))


class TestWhatGetsReviewed:
    def test_an_idea_you_took_is_still_reviewed(self) -> None:
        """`ideas_awaiting_decision` would drop it, and a taken trade is the
        one most worth looking at."""
        journal = FakeJournal()
        idea = _idea()
        journal.record_idea(idea)
        journal.record_decision(
            RecordedDecision(
                idea_id=idea.idea_id,
                action="TOOK",
                reason="liked the order book",
                decided_at=BRIEFED_AT,
            )
        )
        bars = FakeBarStore()
        bars.write_daily_bars([_bar("RELIANCE", TODAY, "1420")], "test")

        review = _cycle(journal, bars).run()

        assert len(review.reviews) == 1
        assert review.reviews[0].decision == "TOOK"

    def test_a_resolved_idea_is_not_reviewed_again(self) -> None:
        journal = FakeJournal()
        idea = _idea()
        journal.record_idea(idea)
        journal.record_outcome(
            RecordedOutcome(
                idea_id=idea.idea_id,
                resolution="TARGET",
                hypothetical_pnl=Money.from_rupees(4500),
                actual_pnl=None,
                resolved_at=CLOSE_TIME,
            )
        )

        review = _cycle(journal, FakeBarStore()).run()

        assert review.reviews == ()

    def test_an_undecided_idea_is_counted(self) -> None:
        journal = FakeJournal()
        journal.record_idea(_idea())
        bars = FakeBarStore()
        bars.write_daily_bars([_bar("RELIANCE", TODAY, "1420")], "test")

        review = _cycle(journal, bars).run()

        assert review.undecided == 1
        assert review.reviews[0].decision == NO_DECISION


class TestWhatItRefusesToSay:
    def test_yesterdays_bar_is_not_reported_as_todays_close(self) -> None:
        """The induced staleness bug.

        `daily_bars(limit=1)` returns the latest bar whatever its date, so a
        feed that stopped on Friday would have Friday's close reported as
        Monday's - every day, looking healthy.
        """
        journal = FakeJournal()
        journal.record_idea(_idea())
        bars = FakeBarStore()
        bars.write_daily_bars([_bar("RELIANCE", date(2026, 10, 16), "1450")], "test")

        review = _cycle(journal, bars).run()

        standing = review.reviews[0].standing
        assert standing.standing is IdeaStanding.UNKNOWN
        assert "no daily bar" in standing.detail
        assert review.unknown == 1

    def test_an_idea_with_no_recorded_levels_says_so(self) -> None:
        """Every idea briefed before migration 0008."""
        journal = FakeJournal()
        journal.record_idea(_idea(stop=None, target=None, expiry=None))
        bars = FakeBarStore()
        bars.write_daily_bars([_bar("RELIANCE", TODAY, "1420")], "test")

        review = _cycle(journal, bars).run()

        assert review.reviews[0].standing.standing is IdeaStanding.UNKNOWN

    def test_the_report_does_not_let_a_level_be_read_as_a_measured_result(self) -> None:
        """The 16:00 scoring run owns P&L, and it arrives fifteen minutes after
        this message. The two must not be confused for each other."""
        journal = FakeJournal()
        journal.record_idea(_idea())
        bars = FakeBarStore()
        bars.write_daily_bars([_bar("RELIANCE", TODAY, "1450")], "test")

        text = _cycle(journal, bars).run().text()

        assert "not that a P&L has been measured" in text
        assert "only it writes an outcome" in text
        assert "16:00" in text


class TestTheReport:
    def test_an_idea_past_its_target_is_counted_as_ready_to_resolve(self) -> None:
        journal = FakeJournal()
        journal.record_idea(_idea())
        bars = FakeBarStore()
        bars.write_daily_bars([_bar("RELIANCE", TODAY, "1455")], "test")

        review = _cycle(journal, bars).run()

        assert review.ready_to_resolve == 1
        assert review.reviews[0].standing.standing is IdeaStanding.PAST_TARGET

    def test_the_invalidation_is_quoted_back_verbatim(self) -> None:
        """The reader judges the thesis, not the model that wrote it."""
        journal = FakeJournal()
        journal.record_idea(_idea())
        bars = FakeBarStore()
        bars.write_daily_bars([_bar("RELIANCE", TODAY, "1420")], "test")

        text = _cycle(journal, bars).run().text()

        assert "closes back inside its prior range" in text

    def test_every_standing_carries_its_numbers(self) -> None:
        journal = FakeJournal()
        journal.record_idea(_idea())
        bars = FakeBarStore()
        bars.write_daily_bars([_bar("RELIANCE", TODAY, "1420")], "test")

        text = _cycle(journal, bars).run().text()

        assert "1,420" in text
        assert "1,400" in text
        assert "1,440" in text

    def test_no_open_ideas_is_a_report_not_silence(self) -> None:
        review = _cycle(FakeJournal(), FakeBarStore()).run()

        assert "No open ideas" in review.text()


class TestDelivery:
    def test_it_is_sent(self) -> None:
        journal = FakeJournal()
        journal.record_idea(_idea())
        bars = FakeBarStore()
        bars.write_daily_bars([_bar("RELIANCE", TODAY, "1420")], "test")
        notifier = FakeNotifier()

        deliver(_cycle(journal, bars).run(), notifier)

        assert len(notifier.briefings) == 1
        assert "POST-CLOSE REVIEW" in notifier.briefings[0]

    def test_a_failed_send_raises_rather_than_reporting_success(self) -> None:
        notifier = FakeNotifier()
        notifier.fail_next_send = True

        try:
            deliver(_cycle(FakeJournal(), FakeBarStore()).run(), notifier)
        except ConnectionError:
            pass
        else:
            raise AssertionError("a failed review was reported as delivered")
