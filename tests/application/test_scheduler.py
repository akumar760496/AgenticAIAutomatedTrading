"""The guard every scheduled job runs behind.

Three behaviours matter and all three are failures, so all three are induced
rather than asserted about the happy path: a job that raises, a notifier that
is also down, and a holiday.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from tests.fakes import FakeJobLog, FakeNotifier, FrozenClock
from trading_desk.application.calendars import TradingCalendar
from trading_desk.application.scheduler import (
    BRIEF,
    SESSIONS_REQUIRED,
    DailyJob,
    JobGuard,
    PollingJob,
    build_scheduler,
    streak_for,
)
from trading_desk.ports.job_log import JobOutcome, JobRun
from trading_desk.ports.notifier import AlertLevel

# 2026-10-19 08:15 IST.
MONDAY_MORNING = datetime(2026, 10, 19, 2, 45, tzinfo=UTC)
DIWALI = date(2026, 11, 9)


def _calendar(holidays: frozenset[date] = frozenset()) -> TradingCalendar:
    return TradingCalendar(holidays=holidays, special_sessions=frozenset(), verified=True)


def _guard(
    *,
    holidays: frozenset[date] = frozenset(),
    moment: datetime = MONDAY_MORNING,
) -> tuple[JobGuard, FakeJobLog, FakeNotifier]:
    job_log = FakeJobLog()
    notifier = FakeNotifier()
    guard = JobGuard(
        calendar=_calendar(holidays),
        clock=FrozenClock(moment),
        notifier=notifier,
        job_log=job_log,
    )
    return guard, job_log, notifier


class TestAJobThatWorks:
    def test_it_runs_and_the_run_is_recorded(self) -> None:
        guard, job_log, _notifier = _guard()
        calls: list[int] = []

        guard.wrap(BRIEF, lambda: calls.append(1), trading_days_only=True)()

        assert calls == [1]
        assert [run.outcome for run in job_log.recorded] == [JobOutcome.RAN]
        assert job_log.recorded[0].trading_date == date(2026, 10, 19)


class TestAJobThatRaises:
    def test_the_failure_does_not_escape_the_guard(self) -> None:
        """Re-raising only removes the job from the scheduler."""
        guard, _log, _notifier = _guard()

        def explode() -> None:
            raise RuntimeError("Postgres is not listening")

        guard.wrap(BRIEF, explode, trading_days_only=True)()  # must not raise

    def test_it_is_logged_as_failed_with_the_reason(self) -> None:
        guard, job_log, _notifier = _guard()

        def explode() -> None:
            raise RuntimeError("Postgres is not listening")

        guard.wrap(BRIEF, explode, trading_days_only=True)()

        assert job_log.recorded[0].outcome is JobOutcome.FAILED
        assert "Postgres is not listening" in job_log.recorded[0].detail

    def test_it_alerts_critical(self) -> None:
        """FR-9.6: a cycle failure reaches the user."""
        guard, _log, notifier = _guard()

        guard.wrap(BRIEF, _explode, trading_days_only=True)()

        level, message = notifier.alerts[0]
        assert level is AlertLevel.CRITICAL
        assert BRIEF in message

    def test_the_run_is_logged_even_when_the_alert_cannot_be_sent(self) -> None:
        """The induced case that fixes the order of the two writes.

        If Telegram is what is broken, alerting first loses the record of the
        original failure as well - and then nothing anywhere says the 08:15
        job died.
        """
        guard, job_log, notifier = _guard()
        notifier.fail_next_alert = True

        guard.wrap(BRIEF, _explode, trading_days_only=True)()

        assert job_log.recorded[0].outcome is JobOutcome.FAILED
        assert notifier.alerts == []


class TestAClosedMarket:
    def test_a_holiday_is_skipped_with_its_reason_and_no_alert(self) -> None:
        guard, job_log, notifier = _guard(
            holidays=frozenset({DIWALI}),
            moment=datetime(2026, 11, 9, 2, 45, tzinfo=UTC),
        )
        calls: list[int] = []

        guard.wrap(BRIEF, lambda: calls.append(1), trading_days_only=True)()

        assert calls == []
        assert job_log.recorded[0].outcome is JobOutcome.SKIPPED
        assert "holiday" in job_log.recorded[0].detail
        assert notifier.alerts == []

    def test_a_skip_is_recorded_rather_than_leaving_a_gap(self) -> None:
        """A holiday and a dead laptop both produce no briefing. Only the log
        can tell them apart."""
        guard, job_log, _notifier = _guard(
            holidays=frozenset({DIWALI}),
            moment=datetime(2026, 11, 9, 2, 45, tzinfo=UTC),
        )

        guard.wrap(BRIEF, _explode, trading_days_only=True)()

        assert len(job_log.recorded) == 1

    def test_a_job_that_is_not_trading_day_only_still_runs(self) -> None:
        """The nightly backup does not care that the market was shut."""
        guard, _log, _notifier = _guard(
            holidays=frozenset({DIWALI}),
            moment=datetime(2026, 11, 9, 2, 45, tzinfo=UTC),
        )
        calls: list[int] = []

        guard.wrap("backup", lambda: calls.append(1), trading_days_only=False)()

        assert calls == [1]


class TestTheStreakRead:
    def test_failed_runs_do_not_count_towards_the_streak(self) -> None:
        job_log = FakeJobLog()
        for day, outcome in (
            (date(2026, 10, 15), JobOutcome.RAN),
            (date(2026, 10, 16), JobOutcome.FAILED),
            (date(2026, 10, 19), JobOutcome.RAN),
        ):
            job_log.record(
                JobRun(
                    job=BRIEF,
                    outcome=outcome,
                    at=datetime(day.year, day.month, day.day, 2, 45, tzinfo=UTC),
                    trading_date=day,
                )
            )

        streak = streak_for(
            job_log,
            BRIEF,
            up_to=date(2026, 10, 19),
            holidays=frozenset(),
            since=date(2026, 9, 1),
        )

        assert streak.length == 1
        assert SESSIONS_REQUIRED == 5

    def test_a_correctly_skipped_holiday_counts_as_the_job_working(self) -> None:
        job_log = FakeJobLog()
        job_log.record(
            JobRun(
                job=BRIEF,
                outcome=JobOutcome.SKIPPED,
                at=MONDAY_MORNING,
                trading_date=date(2026, 10, 19),
                detail="exchange holiday",
            )
        )

        streak = streak_for(
            job_log,
            BRIEF,
            up_to=date(2026, 10, 19),
            holidays=frozenset(),
            since=date(2026, 9, 1),
        )

        assert streak.length == 1


class TestThePollDoesNotFloodTheLog:
    def test_a_successful_poll_writes_nothing(self) -> None:
        """2,880 fires a day. Logging each one buries the 08:15 row."""
        guard, job_log, _notifier = _guard()

        guard.wrap("capture", lambda: None, trading_days_only=False, log_runs=False)()

        assert job_log.recorded == []

    def test_a_failing_poll_is_still_recorded(self) -> None:
        """The exception to the rule, because a failure is why the log exists."""
        guard, job_log, _notifier = _guard()

        guard.wrap("capture", _explode, trading_days_only=False, log_runs=False)()

        assert job_log.recorded[0].outcome is JobOutcome.FAILED

    def test_the_capture_poll_defaults_to_not_logging_runs(self) -> None:
        assert PollingJob(name="capture", every_seconds=30, run=lambda: None).log_runs is False


class TestTheTimetable:
    def test_every_job_is_registered_exactly_once_under_its_own_id(self) -> None:
        guard, _log, _notifier = _guard()
        scheduler = build_scheduler(
            guard=guard,
            daily=[
                DailyJob(name="brief", hour=8, minute=15, run=lambda: None),
                DailyJob(name="review", hour=15, minute=45, run=lambda: None),
            ],
            polling=[PollingJob(name="capture", every_seconds=30, run=lambda: None)],
        )

        ids = sorted(job.id for job in scheduler.get_jobs())
        assert ids == ["brief", "capture", "review"]

    def test_a_polling_job_cannot_overlap_itself(self) -> None:
        """Two concurrent drains would race on the cursor file."""
        guard, _log, _notifier = _guard()
        scheduler = build_scheduler(
            guard=guard,
            daily=[],
            polling=[PollingJob(name="capture", every_seconds=30, run=lambda: None)],
        )

        assert scheduler.get_job("capture").max_instances == 1

    def test_a_daily_job_coalesces_after_a_long_sleep(self) -> None:
        """A laptop shut for six hours should brief once, not six times."""
        guard, _log, _notifier = _guard()
        scheduler = build_scheduler(
            guard=guard,
            daily=[DailyJob(name="brief", hour=8, minute=15, run=lambda: None)],
            polling=[],
        )

        assert scheduler.get_job("brief").coalesce is True


def _explode() -> None:
    raise RuntimeError("Postgres is not listening")
