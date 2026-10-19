"""Verify step 3.8 - scoring, calibration, monitors.

`docs/12` is unusually specific about this step, and the order it asks for is
the order this script runs in: **"verify the maths against a known case before
trusting it on real data."** So the first checks are arithmetic that was worked
out by hand - 70% of 70%-confidence ideas succeeded gives a Brier score of
exactly 0.21 - and only after that does anything touch a database.

The rest induces ways the numbers could look right while being wrong:

- a perfectly calibrated forecaster that carries no information, which must
  **not** open the sizing lock,
- a credit spread run through the debit rulebook, which must raise rather than
  report a winner as a loser,
- costs charged at the entry premium rather than the exit, which understates
  exactly the trades that look best,
- a missing exit premium, which must refuse rather than write a permanent
  append-only row,
- a no-trade rate computed over trading days instead of job runs, which would
  make a switched-off box look disciplined,
- and the sizing lock itself, wired at by introspection rather than trusted.

The database drills run inside a transaction that is rolled back (ISS-024).
"""

from __future__ import annotations

import dataclasses
import inspect
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import Engine, text
from tests.fakes import FakeChainArchive, FakeJobLog, FakeJournal, FakeNotifier, FrozenClock

from scripts.verify._runner import CheckResult
from trading_desk.application.calibration import ALL_SCOPE, CalibrationCycle
from trading_desk.application.health import HealthMonitor
from trading_desk.application.scheduler import BRIEF, CALIBRATION, HEALTH, SCORE
from trading_desk.application.scoring import ScoringCycle
from trading_desk.domain.calibration import ResolvedIdea, calibrate, succeeded
from trading_desk.domain.costs import CostConfig, CostRates
from trading_desk.domain.enums import Direction, Exchange, OptionRight, Segment
from trading_desk.domain.instruments import InstrumentRef
from trading_desk.domain.market import OptionChainSnapshot, OptionQuote, OptionStrike
from trading_desk.domain.monitors import NoTradeRate
from trading_desk.domain.proposals import LegAction
from trading_desk.domain.risk import gate as risk_gate
from trading_desk.domain.risk import sizing as risk_sizing
from trading_desk.domain.scoring import (
    Resolution,
    ScoredLeg,
    credit_exit,
    debit_exit,
    round_trip_cost_at_exit,
    spread_state,
)
from trading_desk.domain.setups.thresholds import load_setup_thresholds
from trading_desk.domain.sizing_lock import MIN_RESOLVED_IDEAS, sizing_lock
from trading_desk.domain.values import Money, Price
from trading_desk.infra.config import load_infra_settings
from trading_desk.infra.cost_config import load_cost_config
from trading_desk.infra.db import create_db_engine
from trading_desk.interfaces.cli import _daily_jobs
from trading_desk.ports.job_log import JobOutcome, JobRun
from trading_desk.ports.journal import RecordedIdea, RecordedLeg, RecordedOutcome

NOW = datetime(2026, 10, 19, 10, 30, tzinfo=UTC)
TODAY = date(2026, 10, 19)
BRIEFED_AT = datetime(2026, 10, 16, 2, 45, tzinfo=UTC)
EXPIRY = date(2026, 11, 26)
FIRST_DAY = date(2026, 10, 1)

LONG_STRIKE = Price.from_rupees("1400")
SHORT_STRIKE = Price.from_rupees("1440")

# The known case from docs/12, and the Brier score it must produce.
KNOWN_CONFIDENCE = 0.7
KNOWN_BRIER = 0.21
KNOWN_TOLERANCE = 1e-9
# Legs in a vertical, and the counts the induced fixtures are built to produce.
LEGS_IN_A_VERTICAL = 2
SESSIONS_FROM_TWO_RUNS = 2
ONE_SESSION = 1
CONFIDENT_WINNERS = 35
INVERTED_WINNERS = 5
SETUP_A_TIME_STOP_DAYS = 5
KNOWN_WINNERS = 7

RELIANCE = InstrumentRef(
    symbol="RELIANCE",
    security_id="2885",
    exchange=Exchange.NSE,
    segment=Segment.EQUITY,
)

FREE = CostConfig(
    rates=CostRates(
        brokerage_per_order=Money.zero(),
        stt_sell_fraction=Decimal(0),
        exchange_txn_fraction=Decimal(0),
        sebi_fraction=Decimal(0),
        stamp_duty_buy_fraction=Decimal(0),
        gst_fraction=Decimal(0),
        slippage_fraction=Decimal(0),
    ),
    verified=False,
    source="verify 3_8",
)


def _known_case() -> tuple[ResolvedIdea, ...]:
    """70 winners and 30 losers, all claimed at 70%."""
    return tuple(
        [ResolvedIdea(KNOWN_CONFIDENCE, True) for _ in range(70)]
        + [ResolvedIdea(KNOWN_CONFIDENCE, False) for _ in range(30)]
    )


def _discriminating(count: int) -> tuple[ResolvedIdea, ...]:
    """Half at 0.9 winning 90%, half at 0.3 winning 30%. A real forecaster."""
    half = count // 2
    confident = [ResolvedIdea(0.9, index < int(half * 0.9)) for index in range(half)]
    doubtful = [
        ResolvedIdea(0.3, index < int((count - half) * 0.3)) for index in range(count - half)
    ]
    return tuple(confident + doubtful)


def _legs(*, credit: bool = False, expiry: date = EXPIRY) -> tuple[RecordedLeg, ...]:
    """A 1400/1440 vertical. Long 1400 at Rs 50, short 1440 at Rs 30."""
    near, far = (LegAction.BUY, LegAction.SELL) if not credit else (LegAction.SELL, LegAction.BUY)
    return (
        RecordedLeg(
            right=OptionRight.CALL,
            action=near,
            strike=LONG_STRIKE,
            expiry=expiry,
            entry_premium=Money.from_rupees("50"),
        ),
        RecordedLeg(
            right=OptionRight.CALL,
            action=far,
            strike=SHORT_STRIKE,
            expiry=expiry,
            entry_premium=Money.from_rupees("30"),
        ),
    )


def _idea(
    *, legs: tuple[RecordedLeg, ...] | None = None, setup_id: str = "SETUP_A"
) -> RecordedIdea:
    return RecordedIdea(
        idea_id=uuid4(),
        cycle_id=uuid4(),
        symbol="RELIANCE",
        setup_id=setup_id,
        direction=Direction.BULLISH,
        rank=1,
        confidence=0.65,
        gate_values={"iv_rank": "40.0 (proxy)"},
        hypothetical_risk=Money.from_rupees("2000"),
        created_at=BRIEFED_AT,
        stop_level=LONG_STRIKE,
        target_level=SHORT_STRIKE,
        expiry=EXPIRY,
        legs=_legs() if legs is None else legs,
    )


def _quote(premium: str) -> OptionQuote:
    mid = Money.from_rupees(premium).paise
    return OptionQuote(
        right=OptionRight.CALL,
        last=Price(paise=mid),
        bid=Price(paise=mid - 50),
        ask=Price(paise=mid + 50),
        volume=1000,
        open_interest=5000,
    )


def _archive(*, long_now: str, short_now: str, on: date = TODAY) -> FakeChainArchive:
    archive = FakeChainArchive()
    stamp = datetime(on.year, on.month, on.day, 9, 45, tzinfo=UTC)
    archive.write(
        OptionChainSnapshot(
            underlying=RELIANCE,
            expiry_date=EXPIRY.isoformat(),
            strikes=(
                OptionStrike(strike=LONG_STRIKE, call=_quote(long_now), put=None),
                OptionStrike(strike=SHORT_STRIKE, call=_quote(short_now), put=None),
            ),
            spot=Price.from_rupees("1430"),
            exchange_time=stamp,
            received_at=stamp,
        )
    )
    return archive


def _cycle(journal: FakeJournal, archive: FakeChainArchive) -> ScoringCycle:
    return ScoringCycle(
        journal=journal,
        chains=archive,
        clock=FrozenClock(NOW),
        thresholds=load_setup_thresholds(),
        costs=FREE,
    )


# ---------------------------------------------------------------- the known case


def _the_brier_score_matches_the_hand_computed_value() -> tuple[bool, str]:
    """docs/12: 70% of 70%-confidence ideas succeeded.

    By hand: 0.7 x (0.7-1)^2 + 0.3 x (0.7-0)^2 = 0.063 + 0.147 = 0.21.
    """
    result = calibrate(_known_case())
    close = abs(result.brier - KNOWN_BRIER) < KNOWN_TOLERANCE
    return close, f"Brier {result.brier:.6f} on 100 ideas at {KNOWN_CONFIDENCE:.0%}"


def _the_decomposition_closes() -> tuple[bool, str]:
    """reliability - resolution + uncertainty must rebuild the Brier score.

    An arithmetic identity, so a mismatch means one of the three is wrong -
    and a wrong resolution term is what would let a useless forecaster look
    informative.
    """
    result = calibrate(_discriminating(100))
    rebuilt = result.reliability - result.resolution + result.uncertainty
    return abs(rebuilt - result.brier) < KNOWN_TOLERANCE, (
        f"{result.reliability:.6f} - {result.resolution:.6f} + "
        f"{result.uncertainty:.6f} = {rebuilt:.6f} vs Brier {result.brier:.6f}"
    )


def _the_reliability_curve_reports_the_known_bands() -> tuple[bool, str]:
    """The 70-80% band must claim 70% and observe 70%, with zero gap."""
    band = next(b for b in calibrate(_known_case()).bins if b.count)
    return abs(band.gap) < KNOWN_TOLERANCE, band.describe()


def _a_perfectly_calibrated_forecaster_with_no_information_is_caught() -> tuple[bool, str]:
    """The failure docs/12 section 6 warns about: confidence clustered at 0.7,
    calibrated perfectly, and carrying nothing. Skill must be 0, not positive."""
    result = calibrate(_known_case())
    caught = not result.beats_baseline and abs(result.skill) < KNOWN_TOLERANCE
    return caught, (
        f"skill {result.skill:+.6f}, resolution {result.resolution:.6f} "
        f"(beats baseline: {result.beats_baseline})"
    )


def _a_discriminating_forecaster_beats_the_baseline() -> tuple[bool, str]:
    """The opposite polarity. If this fails, the check above passes for the
    wrong reason - nothing would ever beat the baseline."""
    result = calibrate(_discriminating(100))
    return result.beats_baseline, (
        f"Brier {result.brier:.4f} vs baseline {result.baseline_brier:.4f}, "
        f"skill {result.skill:+.1%}"
    )


def _an_inverted_forecaster_scores_worse_than_guessing() -> tuple[bool, str]:
    """High confidence on the losers. Skill must go negative."""
    inverted = tuple(
        [ResolvedIdea(0.9, index < INVERTED_WINNERS) for index in range(50)]
        + [ResolvedIdea(0.3, index < CONFIDENT_WINNERS) for index in range(50)]
    )
    result = calibrate(inverted)
    return result.skill < 0, f"skill {result.skill:+.1%} on an inverted forecaster"


def _no_data_is_not_a_perfect_score() -> tuple[bool, str]:
    """A Brier of 0.0 is flawless. Empty input must raise, not return it."""
    try:
        calibrate(())
    except ValueError as error:
        return True, f"refused: {error}"
    return False, "an empty set produced a calibration"


# ---------------------------------------------------------------- the sizing lock


def _the_sizing_lock_is_shut_before_fifty_resolved_ideas() -> tuple[bool, str]:
    """A good score over 20 trades is noise."""
    lock = sizing_lock(calibrate(_discriminating(20)))
    return lock.beats_baseline and not lock.is_open, lock.describe()


def _the_sizing_lock_is_shut_without_skill_however_many_ideas() -> tuple[bool, str]:
    lock = sizing_lock(calibrate(_known_case()))
    return lock.has_enough_samples and not lock.is_open, lock.describe()


def _the_sizing_lock_opens_only_on_both_conditions() -> tuple[bool, str]:
    """And then says sizing is still flat, because opening it is a conversation."""
    lock = sizing_lock(calibrate(_discriminating(MIN_RESOLVED_IDEAS + 10)))
    says_so = "still flat" in lock.describe()
    return lock.is_open and says_so, lock.describe()


def _confidence_cannot_be_wired_into_sizing() -> tuple[bool, str]:
    """FR-7.8 says enforced in code. Introspected rather than asserted: every
    function and dataclass in `domain/risk/sizing.py` and `gate.py` is checked
    for a confidence parameter or field."""
    offenders = []
    for module in (risk_gate, risk_sizing):
        for name, function in inspect.getmembers(module, inspect.isfunction):
            if function.__module__ != module.__name__:
                continue
            if "confidence" in inspect.signature(function).parameters:
                offenders.append(f"{module.__name__}.{name}()")
        for name, kind in inspect.getmembers(module, inspect.isclass):
            if not dataclasses.is_dataclass(kind) or kind.__module__ != module.__name__:
                continue
            if any(field.name == "confidence" for field in dataclasses.fields(kind)):
                offenders.append(f"{module.__name__}.{name}")

    checked = sum(
        1
        for module in (risk_gate, risk_sizing)
        for _ in inspect.getmembers(module, inspect.isfunction)
    )
    return not offenders, (
        f"{checked} callables across risk.gate and risk.sizing, "
        f"{len(offenders)} taking confidence{': ' + ', '.join(offenders) if offenders else ''}"
    )


def _the_lock_itself_carries_no_size() -> tuple[bool, str]:
    """It answers whether confidence has earned the right to inform size. If it
    ever carries a size, the lock has become the thing it guards against."""
    banned = ("size", "quantity", "qty", "lots", "multiplier", "allocation")
    lock = sizing_lock(None)
    names = set(lock.__dataclass_fields__) | {
        name for name in dir(lock) if not name.startswith("_")
    }
    offenders = [name for name in names if any(word in name.lower() for word in banned)]
    return not offenders, f"{len(names)} public names on SizingLock, {len(offenders)} banned"


# ---------------------------------------------------------------- the rulebooks


def _a_credit_spread_is_not_scored_by_the_debit_rules() -> tuple[bool, str]:
    """Induced. A credit spread that captured half its credit is a winner.

    Run through the debit arithmetic the same position reads as a loss, because
    a credit's net entry is negative and "the debit lost" is computed from it.
    The debit rulebook must refuse the structure, not return a number.
    """
    thresholds = load_setup_thresholds()
    # Rs 20 received; closing now costs Rs 10, so Rs 10 captured - the target.
    state = spread_state(
        (
            ScoredLeg(LegAction.SELL, Money.from_rupees("50"), Money.from_rupees("35")),
            ScoredLeg(LegAction.BUY, Money.from_rupees("30"), Money.from_rupees("25")),
        ),
        (LONG_STRIKE, SHORT_STRIKE),
    )
    credit_says = credit_exit(state, thresholds.setup_b.credit_exits)
    try:
        debit_exit(state, thresholds.setup_a.exits, days_to_expiry=20)
    except ValueError as error:
        return credit_says.resolution is Resolution.TARGET, (
            f"credit rulebook: {credit_says.resolution}; debit rulebook refused ({error})"
        )
    return False, "the debit rulebook scored a credit spread instead of refusing"


def _costs_are_charged_on_the_exit_premium() -> tuple[bool, str]:
    """A winning debit spread exits at a higher premium, so its turnover-linked
    charges are larger. Reusing the entry premium understates the cost of
    exactly the trades that look best, which is how a backtest flatters itself.
    """
    rates = load_cost_config().rates
    winner = (
        ScoredLeg(LegAction.BUY, Money.from_rupees("50"), Money.from_rupees("70")),
        ScoredLeg(LegAction.SELL, Money.from_rupees("30"), Money.from_rupees("26")),
    )
    loser = (
        ScoredLeg(LegAction.BUY, Money.from_rupees("50"), Money.from_rupees("40")),
        ScoredLeg(LegAction.SELL, Money.from_rupees("30"), Money.from_rupees("30")),
    )
    high = round_trip_cost_at_exit(winner, 100, rates).total
    low = round_trip_cost_at_exit(loser, 100, rates).total
    return high.paise > low.paise, (
        f"winner's round trip Rs {high.paise / 100:,.2f} vs loser's "
        f"Rs {low.paise / 100:,.2f} at 100 contracts"
    )


def _the_exit_rules_come_from_config_not_code() -> tuple[bool, str]:
    """Rule 7. docs/06's numbers must be in setups.yaml with a date."""
    thresholds = load_setup_thresholds()
    a = thresholds.setup_a.exits
    credit = thresholds.setup_b.credit_exits
    correct = (
        a.target_fraction_of_width == Decimal("0.60")
        and a.stop_fraction_of_debit_lost == Decimal("0.50")
        and a.time_stop_days_to_expiry == SETUP_A_TIME_STOP_DAYS
        and credit.stop_multiple_of_credit == Decimal("2.0")
    )
    return correct, (
        f"Setup A target {a.target_fraction_of_width}, stop "
        f"{a.stop_fraction_of_debit_lost}, time stop {a.time_stop_days_to_expiry}d; "
        f"Setup B credit stop {credit.stop_multiple_of_credit}x"
    )


def _success_is_net_money_not_the_resolution_label() -> tuple[bool, str]:
    """A TARGET that netted a loss after costs is not a success. Costs are ~13%
    of a vertical's best case (ISS-023), so the two genuinely disagree."""
    return (succeeded(1) and not succeeded(0) and not succeeded(-1)), (
        "+1p=success, 0p=failure, -1p=failure"
    )


# ---------------------------------------------------------------- the cycle


def _a_resolved_idea_is_scored_with_gross_costs_and_the_threshold() -> tuple[bool, str]:
    journal = FakeJournal()
    journal.record_idea(_idea())
    report = _cycle(journal, _archive(long_now="70", short_now="26")).run()

    if not report.scored:
        return False, f"nothing scored: {[u.reason for u in report.unscoreable]}"
    row = journal.outcomes[0]
    complete = row.gross_pnl is not None and row.costs is not None and row.measured != ""
    return complete and row.resolution == "TARGET", (
        f"{row.resolution} net {row.hypothetical_pnl.paise}p, "
        f"measured '{row.measured}', basis {row.basis}"
    )


def _a_missing_exit_premium_writes_nothing() -> tuple[bool, str]:
    """Induced, and the most important refusal here. `outcomes` is append-only,
    so a row written from a guessed premium is permanent and indistinguishable
    from a measured one (rule 6)."""
    journal = FakeJournal()
    journal.record_idea(_idea())
    stale = _archive(long_now="70", short_now="26", on=date(2026, 10, 16))
    report = _cycle(journal, stale).run()

    refused = len(journal.outcomes) == 0 and len(report.unscoreable) == 1
    return refused, (
        f"{len(journal.outcomes)} outcome rows written; "
        f"reason: {report.unscoreable[0].reason if report.unscoreable else 'none'}"
    )


def _an_idea_with_no_legs_is_reported_not_scored() -> tuple[bool, str]:
    """Every idea briefed before migration 0009."""
    journal = FakeJournal()
    journal.record_idea(_idea(legs=()))
    report = _cycle(journal, _archive(long_now="70", short_now="26")).run()
    return len(journal.outcomes) == 0 and bool(report.unscoreable), (
        report.unscoreable[0].reason if report.unscoreable else "scored anyway"
    )


def _the_report_does_not_claim_a_realised_fill() -> tuple[bool, str]:
    """The archive holds three snapshots a day, so a stop is seen at 09:30,
    12:30 or 15:15 - not when it was breached. The text must say so."""
    journal = FakeJournal()
    journal.record_idea(_idea())
    text_out = _cycle(journal, _archive(long_now="70", short_now="26")).run().text()
    honest = "Hypothetical" in text_out and "not from an intraday fill" in text_out
    return honest, text_out.strip().splitlines()[-1]


def _calibration_records_a_snapshot_per_scope() -> tuple[bool, str]:
    journal = FakeJournal()
    for index in range(10):
        idea = _idea(setup_id="SETUP_A" if index % 2 else "SETUP_B")
        journal.record_idea(idea)
        journal.record_outcome(_outcome(idea.idea_id, "100" if index < KNOWN_WINNERS else "-100"))
    report = CalibrationCycle(journal=journal, clock=FrozenClock(NOW)).run()
    scopes = {snapshot.scope for snapshot in journal.calibrations}
    return scopes == {ALL_SCOPE, "SETUP_A", "SETUP_B"}, (
        f"{len(journal.calibrations)} snapshots: {sorted(scopes)}; lock: {report.lock.describe()}"
    )


def _outcome(idea_id: UUID, net_rupees: str) -> RecordedOutcome:
    return RecordedOutcome(
        idea_id=idea_id,
        resolution="TARGET" if Money.from_rupees(net_rupees).paise > 0 else "STOP",
        hypothetical_pnl=Money.from_rupees(net_rupees),
        actual_pnl=None,
        resolved_at=NOW,
    )


# ---------------------------------------------------------------- the monitors


def _the_no_trade_monitor_alerts_below_twenty_percent() -> tuple[bool, str]:
    """docs/12's stated check for this step."""
    journal, job_log, notifier = FakeJournal(), FakeJobLog(), FakeNotifier()
    for index in range(20):
        day = FIRST_DAY + timedelta(days=index)
        job_log.record(JobRun(job=BRIEF, outcome=JobOutcome.RAN, at=NOW, trading_date=day))
        if index > 1:  # 18 of 20 briefed: 10% quiet
            journal.record_idea(
                dataclasses.replace(
                    _idea(),
                    created_at=datetime(day.year, day.month, day.day, 3, 30, tzinfo=UTC),
                )
            )
    report = HealthMonitor(
        journal=journal, job_log=job_log, clock=FrozenClock(NOW), notifier=notifier
    ).run()
    return len(notifier.alerts) == 1 and report.needs_alert, (
        f"{report.no_trade.describe()}; {len(notifier.alerts)} alert(s)"
    )


def _the_band_is_measured_over_job_runs_not_trading_days() -> tuple[bool, str]:
    """Induced. Twelve calendar days in the window, the job ran on two and
    briefed on both. Counting days would read 83% quiet and alert; counting
    runs reads 0% over 2 sessions, which is too few to judge.

    This is the version of the bug that makes a switched-off box look like a
    disciplined one.
    """
    journal, job_log, notifier = FakeJournal(), FakeJobLog(), FakeNotifier()
    for index in range(2):
        day = FIRST_DAY + timedelta(days=index)
        job_log.record(JobRun(job=BRIEF, outcome=JobOutcome.RAN, at=NOW, trading_date=day))
        journal.record_idea(
            dataclasses.replace(
                _idea(),
                created_at=datetime(day.year, day.month, day.day, 3, 30, tzinfo=UTC),
            )
        )
    report = HealthMonitor(
        journal=journal, job_log=job_log, clock=FrozenClock(NOW), notifier=notifier
    ).run()
    return report.no_trade.sessions == SESSIONS_FROM_TWO_RUNS and not notifier.alerts, (
        f"{report.no_trade.sessions} sessions counted from job runs "
        f"(not the 12 calendar days in the window), {len(notifier.alerts)} alert(s)"
    )


def _a_failed_or_skipped_run_is_not_a_quiet_session() -> tuple[bool, str]:
    """A failed job produced no briefing at all; a holiday was never a session.
    Counting either as quiet turns an outage into evidence of discipline."""
    journal, job_log, notifier = FakeJournal(), FakeJobLog(), FakeNotifier()
    job_log.record(JobRun(job=BRIEF, outcome=JobOutcome.RAN, at=NOW, trading_date=FIRST_DAY))
    journal.record_idea(
        dataclasses.replace(_idea(), created_at=datetime(2026, 10, 1, 3, 30, tzinfo=UTC))
    )
    for offset, outcome in ((1, JobOutcome.FAILED), (2, JobOutcome.SKIPPED)):
        job_log.record(
            JobRun(
                job=BRIEF,
                outcome=outcome,
                at=NOW,
                trading_date=FIRST_DAY + timedelta(days=offset),
                detail="induced",
            )
        )
    report = HealthMonitor(
        journal=journal, job_log=job_log, clock=FrozenClock(NOW), notifier=notifier
    ).run()
    return report.no_trade.sessions == ONE_SESSION, (
        f"{report.no_trade.sessions} session(s) from 1 RAN + 1 FAILED + 1 SKIPPED"
    )


def _a_thin_sample_does_not_raise_an_alarm() -> tuple[bool, str]:
    """Three quiet days out of four is 75% and is four sessions of noise."""
    rate = NoTradeRate(sessions=4, sessions_with_no_idea=3)
    return not rate.needs_alert and not rate.is_measurable, rate.describe()


# ---------------------------------------------------------------- the timetable


def _the_timetable_runs_scoring_after_the_review() -> tuple[bool, str]:
    """The order is the dependency: the review reports, scoring writes the rows,
    health measures the rate once the day's result is in the journal."""
    times = {job.name: (job.hour, job.minute) for job in _daily_jobs()}
    needed = {SCORE, HEALTH, CALIBRATION}
    missing = needed - set(times)
    if missing:
        return False, f"missing from the timetable: {sorted(missing)}"
    ordered = times["review"] < times[SCORE] < times[HEALTH]
    listing = ", ".join(
        f"{name} {times[name][0]:02d}:{times[name][1]:02d}"
        for name in ("review", SCORE, HEALTH, CALIBRATION)
    )
    return ordered, listing


# ---------------------------------------------------------------- the database


def _the_database_stores_the_legs_with_their_premiums() -> tuple[bool, str]:
    """Real Postgres, inside a rolled-back transaction (ISS-024)."""
    engine: Engine = create_db_engine(load_infra_settings())
    idea_id, cycle_id = uuid4(), uuid4()
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(
                text(
                    """
                    INSERT INTO briefing_ideas (
                        idea_id, cycle_id, symbol, setup_id, direction, structure,
                        rank, confidence, gate_values, hypothetical_risk_paise,
                        invalidation, agent_rationale, used_iv_proxy, created_at
                    ) VALUES (
                        :idea_id, :cycle_id, 'RELIANCE', 'SETUP_A', 'BULLISH', 'spread',
                        1, 0.65, '{}'::jsonb, 200000, 'x', 'y', true, :now
                    )
                    """
                ),
                {"idea_id": idea_id, "cycle_id": cycle_id, "now": NOW},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO briefing_idea_legs (
                        idea_id, leg_index, option_right, action, strike_paise,
                        expiry_date, entry_premium_paise, recorded_at
                    ) VALUES
                        (:idea_id, 0, 'CALL', 'BUY', 14000000, :expiry, 500000, :now),
                        (:idea_id, 1, 'CALL', 'SELL', 14400000, :expiry, 300000, :now)
                    """
                ),
                {"idea_id": idea_id, "expiry": EXPIRY, "now": NOW},
            )
            rows = connection.execute(
                text(
                    """
                    SELECT leg_index, option_right, action, strike_paise,
                           entry_premium_paise
                    FROM briefing_idea_legs WHERE idea_id = :idea_id
                    ORDER BY leg_index
                    """
                ),
                {"idea_id": idea_id},
            ).all()
        finally:
            transaction.rollback()

    detail = "; ".join(
        f"{row[1]} {row[3] / 100:.0f} {row[2]} at {row[4] / 100:.0f}" for row in rows
    )
    return len(rows) == LEGS_IN_A_VERTICAL, f"{len(rows)} legs round-tripped: {detail}"


def _a_zero_premium_is_refused_by_the_database() -> tuple[bool, str]:
    """Induced. A zero premium makes a long leg look worthless and a short one
    free, and both errors flatter the P&L. Only a constraint says so for every
    future writer."""
    engine: Engine = create_db_engine(load_infra_settings())
    idea_id, cycle_id = uuid4(), uuid4()
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(
                text(
                    """
                    INSERT INTO briefing_ideas (
                        idea_id, cycle_id, symbol, setup_id, direction, structure,
                        rank, confidence, gate_values, hypothetical_risk_paise,
                        invalidation, agent_rationale, used_iv_proxy, created_at
                    ) VALUES (
                        :idea_id, :cycle_id, 'RELIANCE', 'SETUP_A', 'BULLISH', 'spread',
                        1, 0.65, '{}'::jsonb, 200000, 'x', 'y', true, :now
                    )
                    """
                ),
                {"idea_id": idea_id, "cycle_id": cycle_id, "now": NOW},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO briefing_idea_legs (
                        idea_id, leg_index, option_right, action, strike_paise,
                        expiry_date, entry_premium_paise, recorded_at
                    ) VALUES (:idea_id, 0, 'CALL', 'BUY', 14000000, :expiry, 0, :now)
                    """
                ),
                {"idea_id": idea_id, "expiry": EXPIRY, "now": NOW},
            )
        except Exception as error:  # the constraint firing is the pass
            return True, f"refused: {type(error).__name__}"
        finally:
            transaction.rollback()
    return False, "a zero entry premium was accepted"


def _the_legs_table_refuses_an_update() -> tuple[bool, str]:
    """Append-only, enforced by the trigger rather than by the adapter."""
    engine: Engine = create_db_engine(load_infra_settings())
    idea_id, cycle_id = uuid4(), uuid4()
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(
                text(
                    """
                    INSERT INTO briefing_ideas (
                        idea_id, cycle_id, symbol, setup_id, direction, structure,
                        rank, confidence, gate_values, hypothetical_risk_paise,
                        invalidation, agent_rationale, used_iv_proxy, created_at
                    ) VALUES (
                        :idea_id, :cycle_id, 'RELIANCE', 'SETUP_A', 'BULLISH', 'spread',
                        1, 0.65, '{}'::jsonb, 200000, 'x', 'y', true, :now
                    )
                    """
                ),
                {"idea_id": idea_id, "cycle_id": cycle_id, "now": NOW},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO briefing_idea_legs (
                        idea_id, leg_index, option_right, action, strike_paise,
                        expiry_date, entry_premium_paise, recorded_at
                    ) VALUES (:idea_id, 0, 'CALL', 'BUY', 14000000, :expiry, 500000, :now)
                    """
                ),
                {"idea_id": idea_id, "expiry": EXPIRY, "now": NOW},
            )
            connection.execute(
                text(
                    "UPDATE briefing_idea_legs SET entry_premium_paise = 1 WHERE idea_id = :idea_id"
                ),
                {"idea_id": idea_id},
            )
        except Exception as error:
            return True, f"refused: {type(error).__name__}"
        finally:
            transaction.rollback()
    return False, "an UPDATE on briefing_idea_legs succeeded"


def _the_legs_table_refuses_a_truncate() -> tuple[bool, str]:
    """TRUNCATE does not fire row-level triggers, which 0005 found by measuring.
    The statement-level trigger is checked in `pg_trigger`, because
    `information_schema.triggers` does not list TRUNCATE triggers at all."""
    engine: Engine = create_db_engine(load_infra_settings())
    with engine.connect() as connection:
        # One transaction for both statements. A bare SELECT autobegins one, so
        # calling begin() afterwards raises rather than nesting.
        transaction = connection.begin()
        try:
            row = connection.execute(
                text(
                    """
                    SELECT tgname FROM pg_trigger
                    WHERE tgrelid = 'briefing_idea_legs'::regclass
                      AND NOT tgisinternal
                      AND tgname = 'trg_briefing_idea_legs_no_truncate'
                    """
                )
            ).first()
            connection.execute(text("TRUNCATE briefing_idea_legs"))
        except Exception as error:
            return row is not None, f"refused: {type(error).__name__}"
        finally:
            transaction.rollback()
    return False, "TRUNCATE on briefing_idea_legs succeeded"


def _outcomes_refuses_an_unknown_basis() -> tuple[bool, str]:
    """SNAPSHOT, EXPIRY or INVALIDATION. A free-text basis would let a future
    writer record 'probably' and nobody would notice."""
    engine: Engine = create_db_engine(load_infra_settings())
    idea_id, cycle_id = uuid4(), uuid4()
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(
                text(
                    """
                    INSERT INTO briefing_ideas (
                        idea_id, cycle_id, symbol, setup_id, direction, structure,
                        rank, confidence, gate_values, hypothetical_risk_paise,
                        invalidation, agent_rationale, used_iv_proxy, created_at
                    ) VALUES (
                        :idea_id, :cycle_id, 'RELIANCE', 'SETUP_A', 'BULLISH', 'spread',
                        1, 0.65, '{}'::jsonb, 200000, 'x', 'y', true, :now
                    )
                    """
                ),
                {"idea_id": idea_id, "cycle_id": cycle_id, "now": NOW},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO outcomes (
                        idea_id, resolution, hypothetical_pnl_paise, resolved_at,
                        recorded_at, basis
                    ) VALUES (
                        :idea_id, 'TARGET', 2400, :now, :now, 'PROBABLY'
                    )
                    """
                ),
                {"idea_id": idea_id, "now": NOW},
            )
        except Exception as error:
            return True, f"refused: {type(error).__name__}"
        finally:
            transaction.rollback()
    return False, "a basis of 'PROBABLY' was accepted"


def _nothing_is_scoreable_yet() -> tuple[bool, str]:
    """The honest state of the real journal, read rather than remembered.

    No idea briefed before migration 0009 has legs, so nothing in the database
    can be scored today. This check exists to make that visible, and it passes
    because reporting zero is correct - it will start reporting real counts
    once the briefing runs with a chain archive behind it.
    """
    engine: Engine = create_db_engine(load_infra_settings())
    with engine.connect() as connection:
        ideas = connection.execute(text("SELECT count(*) FROM briefing_ideas")).scalar()
        legs = connection.execute(text("SELECT count(*) FROM briefing_idea_legs")).scalar()
        outcomes = connection.execute(text("SELECT count(*) FROM outcomes")).scalar()
    return True, (
        f"{ideas} briefed idea(s), {legs} recorded leg(s), {outcomes} outcome(s) - "
        f"scoring begins with the first briefing after migration 0009"
    )


def run() -> list[CheckResult]:
    checks = [
        (
            "the_brier_score_matches_the_hand_computed_value",
            _the_brier_score_matches_the_hand_computed_value,
            f"need {KNOWN_BRIER} on docs/12's known case, computed by hand",
        ),
        (
            "the_decomposition_closes",
            _the_decomposition_closes,
            "need reliability - resolution + uncertainty = Brier",
        ),
        (
            "the_reliability_curve_reports_the_known_bands",
            _the_reliability_curve_reports_the_known_bands,
            "need a zero gap where the claim was exactly right",
        ),
        (
            "a_perfectly_calibrated_forecaster_with_no_information_is_caught",
            _a_perfectly_calibrated_forecaster_with_no_information_is_caught,
            "need skill 0 - calibration alone must not open the lock",
        ),
        (
            "a_discriminating_forecaster_beats_the_baseline",
            _a_discriminating_forecaster_beats_the_baseline,
            "need positive skill, or the check above passes for nothing",
        ),
        (
            "an_inverted_forecaster_scores_worse_than_guessing",
            _an_inverted_forecaster_scores_worse_than_guessing,
            "need negative skill",
        ),
        (
            "no_data_is_not_a_perfect_score",
            _no_data_is_not_a_perfect_score,
            "need a refusal - Brier 0.0 is a flawless forecaster (rule 6)",
        ),
        (
            "the_sizing_lock_is_shut_before_fifty_resolved_ideas",
            _the_sizing_lock_is_shut_before_fifty_resolved_ideas,
            f"need the lock shut under {MIN_RESOLVED_IDEAS} ideas (FR-7.8)",
        ),
        (
            "the_sizing_lock_is_shut_without_skill_however_many_ideas",
            _the_sizing_lock_is_shut_without_skill_however_many_ideas,
            "need both conditions, not just the sample count",
        ),
        (
            "the_sizing_lock_opens_only_on_both_conditions",
            _the_sizing_lock_opens_only_on_both_conditions,
            "need it open, and still saying sizing is flat",
        ),
        (
            "confidence_cannot_be_wired_into_sizing",
            _confidence_cannot_be_wired_into_sizing,
            "need no confidence parameter or field in risk.sizing or risk.gate",
        ),
        (
            "the_lock_itself_carries_no_size",
            _the_lock_itself_carries_no_size,
            "need no size, quantity or multiplier on SizingLock (rule 1)",
        ),
        (
            "a_credit_spread_is_not_scored_by_the_debit_rules",
            _a_credit_spread_is_not_scored_by_the_debit_rules,
            "need the debit rulebook to refuse a credit structure",
        ),
        (
            "costs_are_charged_on_the_exit_premium",
            _costs_are_charged_on_the_exit_premium,
            "need the winner's round trip to cost more than the loser's",
        ),
        (
            "the_exit_rules_come_from_config_not_code",
            _the_exit_rules_come_from_config_not_code,
            "need docs/06's numbers in setups.yaml (rule 7)",
        ),
        (
            "success_is_net_money_not_the_resolution_label",
            _success_is_net_money_not_the_resolution_label,
            "need breakeven to count as a failure (ISS-023)",
        ),
        (
            "a_resolved_idea_is_scored_with_gross_costs_and_the_threshold",
            _a_resolved_idea_is_scored_with_gross_costs_and_the_threshold,
            "need gross, costs and the measured value on the row (FR-7.6, rule 9)",
        ),
        (
            "a_missing_exit_premium_writes_nothing",
            _a_missing_exit_premium_writes_nothing,
            "need 0 outcome rows and a stated reason (rule 6)",
        ),
        (
            "an_idea_with_no_legs_is_reported_not_scored",
            _an_idea_with_no_legs_is_reported_not_scored,
            "need a refusal naming migration 0009",
        ),
        (
            "the_report_does_not_claim_a_realised_fill",
            _the_report_does_not_claim_a_realised_fill,
            "need the snapshot basis said in words",
        ),
        (
            "calibration_records_a_snapshot_per_scope",
            _calibration_records_a_snapshot_per_scope,
            "need ALL plus one row per setup (FR-7.7)",
        ),
        (
            "the_no_trade_monitor_alerts_below_twenty_percent",
            _the_no_trade_monitor_alerts_below_twenty_percent,
            "need exactly 1 alert on a synthetic 10% run (FR-7.9, docs/12)",
        ),
        (
            "the_band_is_measured_over_job_runs_not_trading_days",
            _the_band_is_measured_over_job_runs_not_trading_days,
            "need 2 sessions from 2 runs, not 12 from the calendar",
        ),
        (
            "a_failed_or_skipped_run_is_not_a_quiet_session",
            _a_failed_or_skipped_run_is_not_a_quiet_session,
            "need 1 session from RAN + FAILED + SKIPPED",
        ),
        (
            "a_thin_sample_does_not_raise_an_alarm",
            _a_thin_sample_does_not_raise_an_alarm,
            "need no alert under 10 sessions",
        ),
        (
            "the_timetable_runs_scoring_after_the_review",
            _the_timetable_runs_scoring_after_the_review,
            "need review < score < health on the clock",
        ),
        (
            "the_database_stores_the_legs_with_their_premiums",
            _the_database_stores_the_legs_with_their_premiums,
            "need 2 legs to round-trip through the real table (migration 0009)",
        ),
        (
            "a_zero_premium_is_refused_by_the_database",
            _a_zero_premium_is_refused_by_the_database,
            "need a check constraint, not a convention",
        ),
        (
            "the_legs_table_refuses_an_update",
            _the_legs_table_refuses_an_update,
            "need the append-only trigger to fire",
        ),
        (
            "the_legs_table_refuses_a_truncate",
            _the_legs_table_refuses_a_truncate,
            "need the statement-level trigger too (ISS-019)",
        ),
        (
            "outcomes_refuses_an_unknown_basis",
            _outcomes_refuses_an_unknown_basis,
            "need SNAPSHOT, EXPIRY or INVALIDATION only",
        ),
        (
            "nothing_is_scoreable_yet",
            _nothing_is_scoreable_yet,
            "the honest count, read from the database",
        ),
    ]

    results = []
    for name, check, requirement in checks:
        passed, measured = check()
        results.append(
            CheckResult(name=name, passed=passed, measured=measured, requirement=requirement)
        )
    return results


TITLE = "Step 3.8 - scoring, calibration, monitors"
