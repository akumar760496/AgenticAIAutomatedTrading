"""The timetable, and the guard that every job runs behind.

The timetable itself is the boring part. The guard is the step.

**A job that raises must not take the scheduler with it.** APScheduler will
happily keep running after a job throws, but it will do so silently, and a
silent failure at 08:15 is a morning with no briefing and no explanation. So
every job is wrapped, and the wrapper does three things in a fixed order:

1. **Skip, with a reason, when the market is shut.** A holiday and a dead
   laptop both produce no briefing; only the log can tell them apart, so
   "SKIPPED: exchange holiday" is written rather than nothing.
2. **Write the outcome to the job log, before alerting.** The order matters: if
   Telegram is the thing that is down, the alert raises, and a wrapper that
   alerted first would lose the record of the original failure as well. The
   file write happens first and does not depend on the network.
3. **Alert CRITICAL, and swallow.** FR-9.6 wants a cycle failure to reach the
   user. Re-raising past this point only removes the job from the scheduler.

The job set is plain data, so the timetable can be asserted on without
starting a clock.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from trading_desk.application.calendars import TradingCalendar
from trading_desk.domain.schedule import SessionStreak, consecutive_sessions_run
from trading_desk.domain.timezones import IST, to_ist
from trading_desk.infra.logging import redact
from trading_desk.ports.clock import Clock
from trading_desk.ports.job_log import JobLogPort, JobOutcome, JobRun
from trading_desk.ports.notifier import AlertLevel, NotifierPort

log = logging.getLogger("trading_desk.scheduler")

# Job names. Constants because the job log is keyed on them and a typo would
# produce a streak of zero that looks like a scheduler that never ran.
BRIEF = "brief"
NAG = "nag"
CAPTURE = "capture"
REVIEW = "review"
SNAPSHOT = "snapshot"
BACKUP = "backup"
EXPIRY_REFRESH = "expiry_refresh"
# Step 3.8. Scoring runs after the review so the message you read at 15:45 is
# free of arithmetic, and health runs after scoring so the day's result is
# already in the journal when the no-trade rate is measured.
SCORE = "score"
HEALTH = "health"
CALIBRATION = "calibration"

# `docs/12` step 3.7: five consecutive unattended sessions.
SESSIONS_REQUIRED = 5

# A laptop that wakes at 08:40 should still brief. Ten minutes for the
# fast jobs; the capture poll has nothing to catch up on, so it does not.
MISFIRE_GRACE_SECONDS = 600

# Telegram's long poll is 25s (`application/decision_capture.py`), so a 30s
# interval is one poll with a small gap rather than overlapping polls.
CAPTURE_INTERVAL_SECONDS = 30


@dataclass(frozen=True)
class DailyJob:
    """One job at one IST time."""

    name: str
    hour: int
    minute: int
    run: Callable[[], None]
    trading_days_only: bool = True

    def __post_init__(self) -> None:
        if not 0 <= self.hour <= 23 or not 0 <= self.minute <= 59:  # noqa: PLR2004 - a clock
            raise ValueError(f"{self.name}: {self.hour:02d}:{self.minute:02d} is not a time")


@dataclass(frozen=True)
class PollingJob:
    """One job on a fixed interval, for work that arrives rather than is due.

    `log_runs` defaults to False and the default is the point. A 30-second poll
    fires 2,880 times a day, so recording every one of those would write ~2,880
    lines to the job log daily - and on a non-trading day, 2,880 identical
    SKIPPED lines. The log exists to answer "did the 08:15 job run", and
    burying that under a poll's heartbeat is how it stops being answerable.
    Failures are always recorded whatever this says.
    """

    name: str
    every_seconds: int
    run: Callable[[], None]
    trading_days_only: bool = True
    log_runs: bool = False


class JobGuard:
    """Wraps a job so a failure is logged, alerted, and survivable."""

    def __init__(
        self,
        *,
        calendar: TradingCalendar,
        clock: Clock,
        notifier: NotifierPort,
        job_log: JobLogPort,
    ) -> None:
        self._calendar = calendar
        self._clock = clock
        self._notifier = notifier
        self._log = job_log

    def wrap(
        self,
        name: str,
        run: Callable[[], None],
        *,
        trading_days_only: bool,
        log_runs: bool = True,
    ) -> Callable[[], None]:
        def guarded() -> None:
            now = self._clock.utc_now()
            today = to_ist(now).date()

            closed = self._calendar.reason_closed(today) if trading_days_only else None
            if closed is not None:
                if log_runs:
                    self._record(name, JobOutcome.SKIPPED, now, today, closed)
                log.info("%s skipped: %s", name, closed)
                return

            try:
                run()
            # Bare `Exception` on purpose: catching a known list would let the
            # unknown one kill the scheduler, which is the failure this guards.
            except Exception as error:
                # Always recorded, whatever log_runs says: a failure is the
                # reason the log exists.
                self._fail(name, now, today, error)
                return

            if log_runs:
                self._record(name, JobOutcome.RAN, now, today, "")
            log.info("%s ran", name)

        return guarded

    def _fail(self, name: str, now: datetime, today: date, error: Exception) -> None:
        """Record first, alert second. See the module docstring."""
        detail = redact(f"{type(error).__name__}: {error}")
        self._record(name, JobOutcome.FAILED, now, today, detail)
        log.error("%s failed: %s", name, detail)

        try:
            self._notifier.send_alert(AlertLevel.CRITICAL, f"{name} failed - {detail}")
        # An unreachable notifier must not hide the failure it was reporting.
        except Exception:
            log.exception("%s failed, and the alert could not be delivered either", name)

    def _record(
        self, name: str, outcome: JobOutcome, now: datetime, today: date, detail: str
    ) -> None:
        self._log.record(
            JobRun(job=name, outcome=outcome, at=now, trading_date=today, detail=detail)
        )


def build_scheduler(
    *,
    guard: JobGuard,
    daily: list[DailyJob],
    polling: list[PollingJob],
) -> BlockingScheduler:
    """The timetable, wired. Nothing runs until `start()`."""
    scheduler = BlockingScheduler(timezone=IST)

    for job in daily:
        scheduler.add_job(
            guard.wrap(job.name, job.run, trading_days_only=job.trading_days_only),
            CronTrigger(
                day_of_week="mon-fri" if job.trading_days_only else "*",
                hour=job.hour,
                minute=job.minute,
                timezone=IST,
            ),
            id=job.name,
            misfire_grace_time=MISFIRE_GRACE_SECONDS,
            # One run after a long sleep, not a backlog of six.
            coalesce=True,
            max_instances=1,
        )

    for poll in polling:
        scheduler.add_job(
            guard.wrap(
                poll.name,
                poll.run,
                trading_days_only=poll.trading_days_only,
                log_runs=poll.log_runs,
            ),
            IntervalTrigger(seconds=poll.every_seconds),
            id=poll.name,
            # No grace: a missed poll is caught by the next one, and Telegram
            # holds the update until it is acknowledged.
            misfire_grace_time=None,
            coalesce=True,
            # The cursor file is not safe against two concurrent drains.
            max_instances=1,
        )

    return scheduler


def streak_for(
    job_log: JobLogPort,
    job: str,
    *,
    up_to: date,
    holidays: frozenset[date],
    since: date,
) -> SessionStreak:
    """How many consecutive sessions a job has fired.

    `RAN` and `SKIPPED` both count as fired: a job that correctly skipped a
    holiday did its work. Only `FAILED` breaks a streak, which is the point -
    the question is whether the scheduler is working, not whether the market
    was interesting.
    """
    fired = {
        run.trading_date for run in job_log.runs(job, since) if run.outcome is not JobOutcome.FAILED
    }
    return consecutive_sessions_run(ran_on=fired, up_to=up_to, holidays=holidays)
