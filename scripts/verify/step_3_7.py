"""Verify step 3.7 - the scheduler and the post-close review.

`docs/10`: done when "five consecutive sessions run unattended on schedule",
and `docs/12` adds the part that decides how this script is built: **"verify
from logs, not memory."** Memory is the actual risk here. The natural way to
believe a scheduler works is to remember that it seemed to, and the natural
failure is a job that quietly stopped firing while the laptop was asleep. So
the headline check counts dated rows in the job log, and it reads 0 today.

Everything above it induces a way the timetable could look healthy while being
broken: a job that raises, a notifier that is down at the same moment, a
holiday that looks like a crash, a streak that ended three weeks ago, a review
reading Friday's close as today's, and a bearish idea measured with a bullish
comparison.

The database drills run inside a transaction that is rolled back (ISS-024).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import Engine, text
from tests.fakes import (
    FakeBarStore,
    FakeChainArchive,
    FakeJobLog,
    FakeJournal,
    FakeNotifier,
    FrozenClock,
)

from scripts.verify._runner import CheckResult
from trading_desk.adapters.store.jsonl_job_log import JsonlJobLog
from trading_desk.application.calendars import TradingCalendar, load_trading_calendar
from trading_desk.application.iv_rank import IvRankService
from trading_desk.application.review_cycle import ReviewCycle
from trading_desk.application.scheduler import (
    BRIEF,
    CAPTURE,
    REVIEW,
    SESSIONS_REQUIRED,
    DailyJob,
    JobGuard,
    PollingJob,
    build_scheduler,
    streak_for,
)
from trading_desk.domain.enums import Direction, Exchange, Segment
from trading_desk.domain.instruments import InstrumentRef
from trading_desk.domain.market import Bar
from trading_desk.domain.review import IdeaStanding
from trading_desk.domain.schedule import consecutive_sessions_run
from trading_desk.domain.values import Money, Price
from trading_desk.domain.vol import IvSource
from trading_desk.infra.config import load_infra_settings
from trading_desk.infra.db import create_db_engine
from trading_desk.interfaces.cli import _daily_jobs
from trading_desk.ports.job_log import JobOutcome, JobRun
from trading_desk.ports.journal import RecordedDecision, RecordedIdea

TITLE = "Scheduler and the post-close review"

# Monday 19 October 2026, 08:15 IST.
MONDAY = date(2026, 10, 19)
MONDAY_MORNING = datetime(2026, 10, 19, 2, 45, tzinfo=UTC)
CLOSE_TIME = datetime(2026, 10, 19, 10, 15, tzinfo=UTC)  # 15:45 IST
DIWALI = date(2026, 11, 9)
DIWALI_MORNING = datetime(2026, 11, 9, 2, 45, tzinfo=UTC)

# FR-7.1.
BRIEF_AT = (8, 15)
REVIEW_AT = (15, 45)

# Enough daily bars for the realised-vol proxy to produce a percentile
# (`domain/vol.MIN_SAMPLES` is 30, over a 20-session window).
BARS_FOR_A_PROXY = 80

# Friday plus Monday.
TWO_SESSIONS = 2

# The stop level the database drill writes, in paise (₹1,400.00).
DRILL_STOP_PAISE = 140_000

RELIANCE = InstrumentRef(
    symbol="RELIANCE",
    exchange=Exchange.NSE,
    segment=Segment.EQUITY,
    security_id="2885",
    lot_size=250,
)


def _calendar(holidays: frozenset[date] = frozenset()) -> TradingCalendar:
    return TradingCalendar(holidays=holidays, special_sessions=frozenset(), verified=True)


def _guard(
    *, holidays: frozenset[date] = frozenset(), moment: datetime = MONDAY_MORNING
) -> tuple[JobGuard, FakeJobLog, FakeNotifier]:
    job_log, notifier = FakeJobLog(), FakeNotifier()
    guard = JobGuard(
        calendar=_calendar(holidays),
        clock=FrozenClock(moment),
        notifier=notifier,
        job_log=job_log,
    )
    return guard, job_log, notifier


def _explode() -> None:
    raise RuntimeError("Postgres is not listening")


def _idea(
    *,
    direction: Direction = Direction.BULLISH,
    stop: str = "1400",
    target: str = "1440",
) -> RecordedIdea:
    return RecordedIdea(
        idea_id=uuid4(),
        cycle_id=uuid4(),
        symbol="RELIANCE",
        setup_id="SETUP_A",
        direction=direction,
        rank=1,
        confidence=0.6,
        gate_values={"iv_rank": "40.0 (proxy)"},
        hypothetical_risk=Money.from_rupees(Decimal("5500")),
        created_at=datetime(2026, 10, 16, 2, 45, tzinfo=UTC),
        invalidation="the stock closes back inside its prior range",
        stop_level=Price.from_rupees(stop),
        target_level=Price.from_rupees(target),
        expiry=date(2026, 11, 5),
    )


def _bar(day: date, close: str) -> Bar:
    level = Price.from_rupees(close)
    return Bar(
        instrument=RELIANCE,
        start=datetime(day.year, day.month, day.day, 3, 45, tzinfo=UTC),
        open=level,
        high=level,
        low=level,
        close=level,
        volume=1_000_000,
    )


# ---------------------------------------------------------------- the timetable


def _the_timetable_covers_every_v1_job() -> tuple[bool, str]:
    jobs = _daily_jobs()
    names = {job.name for job in jobs}
    wanted = {BRIEF, "nag", REVIEW, "backup", "expiry_refresh"}

    return wanted <= names, f"{len(jobs)} daily jobs: " + ", ".join(
        f"{job.hour:02d}:{job.minute:02d} {job.name}"
        for job in sorted(jobs, key=lambda item: (item.hour, item.minute))
    )


def _the_briefing_and_review_run_when_fr_7_1_says() -> tuple[bool, str]:
    times = {job.name: (job.hour, job.minute) for job in _daily_jobs()}
    ok = times.get(BRIEF) == BRIEF_AT and times.get(REVIEW) == REVIEW_AT

    return ok, f"brief {times.get(BRIEF)}, review {times.get(REVIEW)} IST"


def _a_poll_cannot_overlap_itself() -> tuple[bool, str]:
    """Two concurrent drains would race on the Telegram cursor file."""
    guard, _log, _notifier = _guard()
    scheduler = build_scheduler(
        guard=guard,
        daily=[],
        polling=[PollingJob(name=CAPTURE, every_seconds=30, run=lambda: None)],
    )

    instances = scheduler.get_job(CAPTURE).max_instances
    return instances == 1, f"max_instances={instances} on the capture poll"


def _a_long_sleep_coalesces_to_one_run() -> tuple[bool, str]:
    """A laptop shut from 07:00 to 13:00 should brief once, not six times."""
    guard, _log, _notifier = _guard()
    scheduler = build_scheduler(
        guard=guard,
        daily=[DailyJob(name=BRIEF, hour=8, minute=15, run=lambda: None)],
        polling=[],
    )

    job = scheduler.get_job(BRIEF)
    return job.coalesce is True, f"coalesce={job.coalesce}, grace={job.misfire_grace_time}s"


# ----------------------------------------------------------------- the guard


def _a_failing_job_does_not_stop_the_scheduler() -> tuple[bool, str]:
    """Induced. APScheduler keeps running after a throw but says nothing."""
    guard, job_log, _notifier = _guard()
    try:
        guard.wrap(BRIEF, _explode, trading_days_only=True)()
    except RuntimeError:
        return False, "the failure escaped the guard and would kill the job"

    recorded = job_log.recorded[0]
    return recorded.outcome is JobOutcome.FAILED, (
        f"logged {recorded.outcome} with the reason, scheduler survived"
    )


def _a_failure_alerts_critical() -> tuple[bool, str]:
    """FR-9.6."""
    guard, _log, notifier = _guard()
    guard.wrap(BRIEF, _explode, trading_days_only=True)()

    if not notifier.alerts:
        return False, "no alert was sent"
    level, message = notifier.alerts[0]
    return str(level) == "CRITICAL", f"{level}: {message[:48]}"


def _the_failure_is_logged_even_when_the_alert_is_down() -> tuple[bool, str]:
    """Induced, and it fixes the order of two writes.

    If Telegram is the thing that is broken, alerting before logging loses the
    record of the original failure as well - and then nothing anywhere says
    the 08:15 job died.
    """
    guard, job_log, notifier = _guard()
    notifier.fail_next_alert = True
    guard.wrap(BRIEF, _explode, trading_days_only=True)()

    logged = bool(job_log.recorded) and job_log.recorded[0].outcome is JobOutcome.FAILED
    return logged and not notifier.alerts, (
        "notifier down: 1 FAILED row on disk, 0 alerts delivered"
        if logged
        else "the failure was lost with the alert"
    )


def _a_holiday_is_skipped_with_a_reason() -> tuple[bool, str]:
    """A holiday and a dead laptop both produce no briefing."""
    guard, job_log, notifier = _guard(holidays=frozenset({DIWALI}), moment=DIWALI_MORNING)
    calls: list[int] = []
    guard.wrap(BRIEF, lambda: calls.append(1), trading_days_only=True)()

    recorded = job_log.recorded[0]
    ok = not calls and recorded.outcome is JobOutcome.SKIPPED and not notifier.alerts
    return ok, f'{recorded.outcome}: "{recorded.detail}", 0 alerts'


# ----------------------------------------------------------------- the streak


def _the_streak_counts_sessions_not_days() -> tuple[bool, str]:
    """Friday then Monday is two consecutive sessions, not a three-day gap."""
    streak = consecutive_sessions_run(
        ran_on=[date(2026, 10, 16), MONDAY], up_to=MONDAY, holidays=frozenset()
    )

    return streak.length == TWO_SESSIONS, f"Fri + Mon = {streak.length} sessions"


def _a_missed_trading_day_breaks_the_streak() -> tuple[bool, str]:
    """Induced. The opposite polarity of the check above: if weekends do not
    break a streak, a genuine miss must still."""
    streak = consecutive_sessions_run(
        ran_on=[date(2026, 10, 14), date(2026, 10, 16)], up_to=MONDAY, holidays=frozenset()
    )

    return streak.length == 1, f"Wed + Fri, Thu missed = {streak.length} session"


def _a_stale_streak_is_reported_as_stale() -> tuple[bool, str]:
    """Five in a row three weeks ago is not a working scheduler, and no single
    number can say that."""
    old = [date(2026, 9, 21) + timedelta(days=offset) for offset in range(5)]
    streak = consecutive_sessions_run(ran_on=old, up_to=MONDAY, holidays=frozenset())

    stale = streak.length == SESSIONS_REQUIRED and streak.sessions_since_latest > SESSIONS_REQUIRED
    return stale, streak.describe(SESSIONS_REQUIRED)


def _a_failed_run_does_not_count_as_a_session() -> tuple[bool, str]:
    job_log = FakeJobLog()
    for day, outcome in (
        (date(2026, 10, 15), JobOutcome.RAN),
        (date(2026, 10, 16), JobOutcome.FAILED),
        (MONDAY, JobOutcome.RAN),
    ):
        job_log.record(
            JobRun(
                job=BRIEF,
                outcome=outcome,
                at=datetime(day.year, day.month, day.day, 2, 45, tzinfo=UTC),
                trading_date=day,
            )
        )

    streak = streak_for(job_log, BRIEF, up_to=MONDAY, holidays=frozenset(), since=date(2026, 9, 1))
    return streak.length == 1, f"RAN, FAILED, RAN -> {streak.length} session"


# ----------------------------------------------------------------- the review


def _the_review_refuses_a_stale_bar() -> tuple[bool, str]:
    """Induced. `daily_bars(limit=1)` returns the latest bar whatever its date,
    so a feed that stopped on Friday would report Friday's close as today's -
    every day, looking healthy (rule 6)."""
    journal = FakeJournal()
    journal.record_idea(_idea())
    bars = FakeBarStore()
    bars.write_daily_bars([_bar(date(2026, 10, 16), "1450")], "verify")

    review = ReviewCycle(journal=journal, bars=bars, clock=FrozenClock(CLOSE_TIME)).run()

    standing = review.reviews[0].standing
    ok = standing.standing is IdeaStanding.UNKNOWN
    return ok, f"{standing.standing}: {standing.detail}"


def _a_bearish_idea_is_measured_the_right_way_round() -> tuple[bool, str]:
    """Induced. A bearish idea has its target *below* its stop, so the bullish
    comparison reports INSIDE_LEVELS on an idea that worked perfectly."""
    journal = FakeJournal()
    journal.record_idea(_idea(direction=Direction.BEARISH, stop="1440", target="1400"))
    bars = FakeBarStore()
    bars.write_daily_bars([_bar(MONDAY, "1300")], "verify")

    review = ReviewCycle(journal=journal, bars=bars, clock=FrozenClock(CLOSE_TIME)).run()

    standing = review.reviews[0].standing
    ok = standing.standing is IdeaStanding.PAST_TARGET
    return ok, f"bearish, close 1300 vs target 1400 -> {standing.standing}"


def _the_review_includes_the_trades_you_took() -> tuple[bool, str]:
    """`ideas_awaiting_decision` would drop them, and a taken trade is the one
    most worth reviewing."""
    journal = FakeJournal()
    idea = _idea()
    journal.record_idea(idea)
    journal.record_decision(
        RecordedDecision(
            idea_id=idea.idea_id,
            action="TOOK",
            reason="liked the order book",
            decided_at=MONDAY_MORNING,
        )
    )
    bars = FakeBarStore()
    bars.write_daily_bars([_bar(MONDAY, "1420")], "verify")

    review = ReviewCycle(journal=journal, bars=bars, clock=FrozenClock(CLOSE_TIME)).run()

    ok = len(review.reviews) == 1 and review.reviews[0].decision == "TOOK"
    return ok, f"{len(review.reviews)} reviewed, decision {review.reviews[0].decision}"


def _the_review_does_not_claim_to_have_scored_anything() -> tuple[bool, str]:
    """Step 3.8 owns P&L. Said in the message every day, so no reader infers
    that scorekeeping has started."""
    journal = FakeJournal()
    journal.record_idea(_idea())
    bars = FakeBarStore()
    bars.write_daily_bars([_bar(MONDAY, "1455")], "verify")

    review = ReviewCycle(journal=journal, bars=bars, clock=FrozenClock(CLOSE_TIME)).run()
    text = review.text()

    ok = "not that a P&L has been measured" in text and review.ready_to_resolve == 1
    return ok, f"{review.ready_to_resolve} ready to resolve, 0 outcomes written"


# ----------------------------------------------------------------- the IV rank


def _iv_rank_now_reaches_the_cycle() -> tuple[bool, str]:
    """Until this step the briefing was given `iv_ranks={}` and every idea died
    at the volatility gate. The service has to return a usable number from what
    the database actually holds today, which is daily bars and no chain history.
    """
    bars = FakeBarStore()
    start = date(2026, 6, 1)
    closes = [1400 + (index % 7) * 12 for index in range(BARS_FOR_A_PROXY)]
    bars.write_daily_bars(
        [_bar(start + timedelta(days=index), str(close)) for index, close in enumerate(closes)],
        "verify",
    )

    service = IvRankService(archive=FakeChainArchive(), bars=bars, notifier=FakeNotifier())
    rank = service.rank_for("RELIANCE")

    ok = rank.is_available and rank.source is IvSource.PROXY
    return ok, (
        f"{rank.value:.1f} from {rank.sample_count} samples ({rank.source})"
        if rank.is_available
        else f"unavailable: {rank.reason}"
    )


def _a_proxy_rank_is_never_silently_presented_as_real() -> tuple[bool, str]:
    """The provenance has to survive, because months of proxy-derived ideas
    would otherwise be indistinguishable from real ones in 3.8's maths."""
    bars = FakeBarStore()
    start = date(2026, 6, 1)
    bars.write_daily_bars(
        [
            _bar(start + timedelta(days=index), str(1400 + (index % 7) * 12))
            for index in range(BARS_FOR_A_PROXY)
        ],
        "verify",
    )
    report = IvRankService(archive=FakeChainArchive(), bars=bars, notifier=FakeNotifier()).run(
        ["RELIANCE"]
    )

    return report.actual_count == 0 and report.proxy_count == 1, report.summary()


# ----------------------------------------------------------------- the database


def _the_journal_stores_the_three_new_columns() -> tuple[bool, str]:
    """Migration 0008, against the real table. Rolled back (ISS-024).

    An in-memory journal cannot catch a column-shape mismatch, and the levels
    are the only thing 3.8 will have to score with.
    """
    engine: Engine = create_db_engine(load_infra_settings())
    idea_id = uuid4()
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(
                text(
                    """
                    INSERT INTO briefing_ideas (
                        idea_id, cycle_id, symbol, setup_id, direction, structure,
                        rank, confidence, gate_values, hypothetical_risk_paise,
                        invalidation, agent_rationale, used_iv_proxy, created_at,
                        stop_level_paise, target_level_paise, expiry_date
                    ) VALUES (
                        :idea_id, :cycle_id, 'RELIANCE', 'SETUP_A', 'BULLISH', 'spread',
                        1, 0.6, '{}'::jsonb, 550000,
                        'closes back inside the range', 'order win', true, :now,
                        :stop, 144000, :expiry
                    )
                    """
                ),
                {
                    "idea_id": idea_id,
                    "cycle_id": uuid4(),
                    "now": CLOSE_TIME,
                    "stop": DRILL_STOP_PAISE,
                    "expiry": date(2026, 11, 5),
                },
            )
            row = (
                connection.execute(
                    text(
                        "SELECT stop_level_paise, target_level_paise, expiry_date "
                        "FROM briefing_ideas WHERE idea_id = :idea_id"
                    ),
                    {"idea_id": idea_id},
                )
                .mappings()
                .one()
            )
            measured = (
                f"stop {row['stop_level_paise']}p, target {row['target_level_paise']}p, "
                f"expiry {row['expiry_date']}"
            )
            ok = row["stop_level_paise"] == DRILL_STOP_PAISE
        finally:
            transaction.rollback()
    return ok, measured


def _a_zero_level_is_refused_by_the_database() -> tuple[bool, str]:
    """Induced. The columns are nullable, so "no level" is legal - but a level
    of ₹0 is not, and only a constraint can say so for every future writer."""
    engine: Engine = create_db_engine(load_infra_settings())
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(
                text(
                    """
                    INSERT INTO briefing_ideas (
                        idea_id, cycle_id, symbol, setup_id, direction, structure,
                        rank, confidence, gate_values, hypothetical_risk_paise,
                        invalidation, agent_rationale, used_iv_proxy, created_at,
                        stop_level_paise
                    ) VALUES (
                        :idea_id, :cycle_id, 'RELIANCE', 'SETUP_A', 'BULLISH', 'spread',
                        1, 0.6, '{}'::jsonb, 550000, 'x', 'y', true, :now, 0
                    )
                    """
                ),
                {"idea_id": uuid4(), "cycle_id": uuid4(), "now": CLOSE_TIME},
            )
        except Exception as error:  # the constraint firing is the pass
            return True, f"refused: {type(error).__name__}"
        finally:
            transaction.rollback()
    return False, "a stop level of 0 paise was accepted"


def _five_unattended_sessions() -> tuple[bool, str]:
    """The done-when, read from the job log on disk rather than from memory."""
    calendar = load_trading_calendar()
    today = datetime.now(UTC).date()
    streak = streak_for(
        JsonlJobLog(),
        BRIEF,
        up_to=today,
        holidays=calendar.holidays,
        since=today - timedelta(days=60),
    )

    return streak.length >= SESSIONS_REQUIRED, streak.describe(SESSIONS_REQUIRED)


def run() -> list[CheckResult]:
    checks = [
        (
            "the_timetable_covers_every_v1_job",
            _the_timetable_covers_every_v1_job,
            "need brief, nag, review, backup and expiry refresh on the clock",
        ),
        (
            "the_briefing_and_review_run_when_fr_7_1_says",
            _the_briefing_and_review_run_when_fr_7_1_says,
            "need 08:15 and 15:45 IST (FR-7.1)",
        ),
        (
            "a_poll_cannot_overlap_itself",
            _a_poll_cannot_overlap_itself,
            "need max_instances=1 - two drains race on the cursor",
        ),
        (
            "a_long_sleep_coalesces_to_one_run",
            _a_long_sleep_coalesces_to_one_run,
            "need one run after a sleep, not a backlog",
        ),
        (
            "a_failing_job_does_not_stop_the_scheduler",
            _a_failing_job_does_not_stop_the_scheduler,
            "need the throw caught, logged and survived",
        ),
        (
            "a_failure_alerts_critical",
            _a_failure_alerts_critical,
            "need a CRITICAL alert (FR-9.6)",
        ),
        (
            "the_failure_is_logged_even_when_the_alert_is_down",
            _the_failure_is_logged_even_when_the_alert_is_down,
            "need the disk write before the network one",
        ),
        (
            "a_holiday_is_skipped_with_a_reason",
            _a_holiday_is_skipped_with_a_reason,
            "need SKIPPED with a reason and no alert",
        ),
        (
            "the_streak_counts_sessions_not_days",
            _the_streak_counts_sessions_not_days,
            "need a weekend not to break a streak",
        ),
        (
            "a_missed_trading_day_breaks_the_streak",
            _a_missed_trading_day_breaks_the_streak,
            "need the opposite polarity to hold too",
        ),
        (
            "a_stale_streak_is_reported_as_stale",
            _a_stale_streak_is_reported_as_stale,
            f"need staleness alongside the length of {SESSIONS_REQUIRED}",
        ),
        (
            "a_failed_run_does_not_count_as_a_session",
            _a_failed_run_does_not_count_as_a_session,
            "need FAILED to break the streak",
        ),
        (
            "the_review_refuses_a_stale_bar",
            _the_review_refuses_a_stale_bar,
            "need UNKNOWN, never last week's close as today's",
        ),
        (
            "a_bearish_idea_is_measured_the_right_way_round",
            _a_bearish_idea_is_measured_the_right_way_round,
            "need PAST_TARGET - the direction-blind bug reads INSIDE_LEVELS",
        ),
        (
            "the_review_includes_the_trades_you_took",
            _the_review_includes_the_trades_you_took,
            "need decided ideas reviewed, not only undecided ones",
        ),
        (
            "the_review_does_not_claim_to_have_scored_anything",
            _the_review_does_not_claim_to_have_scored_anything,
            "need P&L left to 3.8 and said so in the message",
        ),
        (
            "iv_rank_now_reaches_the_cycle",
            _iv_rank_now_reaches_the_cycle,
            "need a usable rank from bars alone - no chain history exists yet",
        ),
        (
            "a_proxy_rank_is_never_silently_presented_as_real",
            _a_proxy_rank_is_never_silently_presented_as_real,
            "need the PROXY provenance to survive to 3.8",
        ),
        (
            "the_journal_stores_the_three_new_columns",
            _the_journal_stores_the_three_new_columns,
            "need the real table to take stop, target and expiry (migration 0008)",
        ),
        (
            "a_zero_level_is_refused_by_the_database",
            _a_zero_level_is_refused_by_the_database,
            "need a check constraint, not a convention",
        ),
        (
            "five_unattended_sessions",
            _five_unattended_sessions,
            f"need {SESSIONS_REQUIRED} - the done-when, read from the log",
        ),
    ]
    results = []
    for name, check, requirement in checks:
        passed, measured = check()
        results.append(
            CheckResult(name=name, passed=passed, measured=measured, requirement=requirement)
        )
    return results
