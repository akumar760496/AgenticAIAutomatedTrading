"""The no-trade monitor, wired to the job log and the journal.

The test this file exists for is
`test_days_the_scheduler_never_ran_are_not_counted_as_quiet_sessions`. Counting
trading days instead of job runs is the subtle version of this bug: three days
with the box switched off would read as three disciplined no-trade sessions, and
a dead feed would look like a well-behaved system.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

from tests.fakes import FakeJobLog, FakeJournal, FakeNotifier, FrozenClock
from trading_desk.application.health import HealthMonitor
from trading_desk.application.scheduler import BRIEF, NAG
from trading_desk.domain.enums import Direction
from trading_desk.domain.values import Money
from trading_desk.ports.job_log import JobOutcome, JobRun
from trading_desk.ports.journal import RecordedDecision, RecordedIdea

NOW = datetime(2026, 10, 19, 10, 30, tzinfo=UTC)
FIRST_DAY = date(2026, 10, 1)


def ran(job_log: FakeJobLog, day: date, outcome: JobOutcome = JobOutcome.RAN) -> None:
    job_log.record(
        JobRun(
            job=BRIEF,
            outcome=outcome,
            at=datetime(day.year, day.month, day.day, 2, 45, tzinfo=UTC),
            trading_date=day,
        )
    )


def briefed(journal: FakeJournal, day: date, *, answered: bool = False) -> None:
    idea = RecordedIdea(
        idea_id=uuid4(),
        cycle_id=uuid4(),
        symbol="RELIANCE",
        setup_id="SETUP_A",
        direction=Direction.BULLISH,
        rank=1,
        confidence=0.6,
        gate_values={},
        hypothetical_risk=Money.from_rupees("2000"),
        # 09:00 IST, safely inside the IST day whichever way it is converted.
        created_at=datetime(day.year, day.month, day.day, 3, 30, tzinfo=UTC),
    )
    journal.record_idea(idea)
    if answered:
        journal.record_decision(
            RecordedDecision(
                idea_id=idea.idea_id,
                action="SKIPPED",
                reason="already moved too far",
                decided_at=NOW,
            )
        )


def monitor(journal: FakeJournal, job_log: FakeJobLog, notifier: FakeNotifier) -> HealthMonitor:
    return HealthMonitor(
        journal=journal,
        job_log=job_log,
        clock=FrozenClock(NOW),
        notifier=notifier,
    )


class TestTheNoTradeRate:
    def test_a_rate_inside_the_band_raises_no_alert(self) -> None:
        journal, job_log, notifier = FakeJournal(), FakeJobLog(), FakeNotifier()
        for index in range(20):
            day = FIRST_DAY + timedelta(days=index)
            ran(job_log, day)
            if index % 2 == 0:  # ideas on half the sessions: 50% quiet
                briefed(journal, day)

        report = monitor(journal, job_log, notifier).run()

        assert report.no_trade.sessions == 20
        assert report.no_trade.rate == 0.5
        assert not report.needs_alert
        assert notifier.alerts == []

    def test_briefing_every_session_alerts_as_too_loose(self) -> None:
        journal, job_log, notifier = FakeJournal(), FakeJobLog(), FakeNotifier()
        for index in range(20):
            day = FIRST_DAY + timedelta(days=index)
            ran(job_log, day)
            briefed(journal, day)

        report = monitor(journal, job_log, notifier).run()

        assert report.needs_alert
        assert len(notifier.alerts) == 1
        assert "TOO LOOSE" in notifier.alerts[0][1]

    def test_the_synthetic_run_below_twenty_percent_fires_the_alert(self) -> None:
        """docs/12's stated check for this step."""
        journal, job_log, notifier = FakeJournal(), FakeJobLog(), FakeNotifier()
        for index in range(20):
            day = FIRST_DAY + timedelta(days=index)
            ran(job_log, day)
            if index > 1:  # 18 of 20 produced ideas: 10% quiet
                briefed(journal, day)

        report = monitor(journal, job_log, notifier).run()

        assert report.no_trade.rate is not None
        assert report.no_trade.rate < 0.20
        assert len(notifier.alerts) == 1

    def test_going_quiet_alerts_and_points_at_the_feeds_first(self) -> None:
        journal, job_log, notifier = FakeJournal(), FakeJobLog(), FakeNotifier()
        for index in range(20):
            ran(job_log, FIRST_DAY + timedelta(days=index))
        briefed(journal, FIRST_DAY)  # one idea in twenty sessions

        monitor(journal, job_log, notifier).run()

        assert "TOO QUIET" in notifier.alerts[0][1]
        assert "check the feeds" in notifier.alerts[0][1]


class TestWhatCountsAsASession:
    def test_days_the_scheduler_never_ran_are_not_counted_as_quiet_sessions(self) -> None:
        """The bug that would make a switched-off box look disciplined.

        Twelve trading days in the window, the job ran on two of them and
        briefed on both. The rate must be 0% over 2 sessions, not 83% over 12.
        """
        journal, job_log, notifier = FakeJournal(), FakeJobLog(), FakeNotifier()
        for index in range(2):
            day = FIRST_DAY + timedelta(days=index)
            ran(job_log, day)
            briefed(journal, day)

        report = monitor(journal, job_log, notifier).run()

        assert report.no_trade.sessions == 2
        assert report.no_trade.sessions_with_no_idea == 0

    def test_a_skipped_holiday_is_not_a_quiet_session(self) -> None:
        """The gates never saw that day, so it belongs in no denominator."""
        journal, job_log, notifier = FakeJournal(), FakeJobLog(), FakeNotifier()
        ran(job_log, FIRST_DAY)
        briefed(journal, FIRST_DAY)
        ran(job_log, FIRST_DAY + timedelta(days=1), JobOutcome.SKIPPED)

        report = monitor(journal, job_log, notifier).run()
        assert report.no_trade.sessions == 1

    def test_a_failed_run_is_not_a_quiet_session_either(self) -> None:
        """A failed job produced no briefing at all. Counting it as quiet would
        turn an outage into evidence of discipline."""
        journal, job_log, notifier = FakeJournal(), FakeJobLog(), FakeNotifier()
        ran(job_log, FIRST_DAY)
        briefed(journal, FIRST_DAY)
        ran(job_log, FIRST_DAY + timedelta(days=1), JobOutcome.FAILED)

        report = monitor(journal, job_log, notifier).run()
        assert report.no_trade.sessions == 1

    def test_another_job_s_runs_are_not_counted(self) -> None:
        journal, job_log, notifier = FakeJournal(), FakeJobLog(), FakeNotifier()
        ran(job_log, FIRST_DAY)
        briefed(journal, FIRST_DAY)
        job_log.record(JobRun(job=NAG, outcome=JobOutcome.RAN, at=NOW, trading_date=FIRST_DAY))

        assert monitor(journal, job_log, notifier).run().no_trade.sessions == 1

    def test_two_ideas_on_one_day_is_still_one_productive_session(self) -> None:
        journal, job_log, notifier = FakeJournal(), FakeJobLog(), FakeNotifier()
        ran(job_log, FIRST_DAY)
        briefed(journal, FIRST_DAY)
        briefed(journal, FIRST_DAY)

        report = monitor(journal, job_log, notifier).run()
        assert report.no_trade.sessions == 1
        assert report.no_trade.sessions_with_no_idea == 0


class TestNotEnoughData:
    def test_a_fresh_install_raises_no_alarm(self) -> None:
        journal, job_log, notifier = FakeJournal(), FakeJobLog(), FakeNotifier()
        report = monitor(journal, job_log, notifier).run()

        assert report.no_trade.rate is None
        assert not report.needs_alert
        assert notifier.alerts == []

    def test_four_sessions_is_not_enough_to_judge(self) -> None:
        journal, job_log, notifier = FakeJournal(), FakeJobLog(), FakeNotifier()
        for index in range(4):
            ran(job_log, FIRST_DAY + timedelta(days=index))

        report = monitor(journal, job_log, notifier).run()
        assert not report.needs_alert


class TestEngagement:
    def test_a_skipped_decision_counts_as_engagement(self) -> None:
        """The user read the idea and rejected it, which is the system working.
        Counting only TOOK would measure appetite."""
        journal, job_log, notifier = FakeJournal(), FakeJobLog(), FakeNotifier()
        ran(job_log, FIRST_DAY)
        briefed(journal, FIRST_DAY, answered=True)

        report = monitor(journal, job_log, notifier).run()
        assert report.engagement.answered_fraction == 1.0

    def test_unanswered_briefings_show_as_ignored(self) -> None:
        journal, job_log, notifier = FakeJournal(), FakeJobLog(), FakeNotifier()
        ran(job_log, FIRST_DAY)
        for _ in range(4):
            briefed(journal, FIRST_DAY)
        briefed(journal, FIRST_DAY, answered=True)

        report = monitor(journal, job_log, notifier).run()
        assert report.engagement.answered_fraction == 0.2
        assert report.engagement.fails_gate_two

    def test_engagement_alone_does_not_raise_an_alert(self) -> None:
        """It feeds docs/06's Gate 2, which is a decision for the user to make
        about the project, not an operational fault to be woken for."""
        journal, job_log, notifier = FakeJournal(), FakeJobLog(), FakeNotifier()
        for index in range(20):
            day = FIRST_DAY + timedelta(days=index)
            ran(job_log, day)
            if index % 2 == 0:
                briefed(journal, day)  # 50% quiet, inside the band

        report = monitor(journal, job_log, notifier).run()
        assert report.engagement.fails_gate_two
        assert notifier.alerts == []


class TestTheReport:
    def test_both_numbers_appear_in_the_text(self) -> None:
        journal, job_log, notifier = FakeJournal(), FakeJobLog(), FakeNotifier()
        ran(job_log, FIRST_DAY)
        briefed(journal, FIRST_DAY)

        text = monitor(journal, job_log, notifier).run().text()
        assert "no-trade rate" in text
        assert "engagement" in text
