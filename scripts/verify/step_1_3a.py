"""Verify step 1.3a - market data schema and the bar builder.

The websocket adapter is step 1.3b and needs a live session. What is checkable
today: the hypertables exist with the right column types, and the bar builder
behaves correctly at the boundaries where it is easy to be subtly wrong.

The schema checks run against the real database rather than the migration source,
because a migration that was written correctly and never applied looks identical
to one that was applied.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import text

from scripts.verify._runner import CheckResult
from trading_desk.application.exchange_rules import load_exchange_rules
from trading_desk.domain.bars import RejectReason, build_bars
from trading_desk.domain.enums import Exchange, Segment
from trading_desk.domain.instruments import InstrumentRef
from trading_desk.domain.market import Tick
from trading_desk.domain.sessions import session_for
from trading_desk.domain.timezones import IST
from trading_desk.domain.values import Price
from trading_desk.infra.config import load_infra_settings
from trading_desk.infra.db import create_db_engine

TITLE = "Market data schema and bar builder"

EXPECTED_HYPERTABLES = {"bars_1d", "bars_1m", "bars_5m", "ticks"}
SESSION_MINUTES = 375  # 09:15 to 15:30

# Expected totals for the two bar-builder fixtures below, named so the check reads
# as a statement about the fixture rather than a bare number.
VOLUME_AFTER_DROPPING_A_LATE_TICK = 12  # 5 + 7, with the 1000-unit late tick excluded
UNITS_IN_THE_ACCOUNTING_FIXTURE = 40  # four ticks of 10

# Every column that holds money must be bigint paise. A float here rounds silently
# and no later code can detect it (CLAUDE.md rule 4).
PRICE_COLUMNS = {
    "bars_1m": ["open_paise", "high_paise", "low_paise", "close_paise"],
    "bars_5m": ["open_paise", "high_paise", "low_paise", "close_paise"],
    "bars_1d": ["open_paise", "high_paise", "low_paise", "close_paise"],
    "ticks": ["price_paise"],
}

INSTRUMENT = InstrumentRef(
    symbol="RELIANCE",
    exchange=Exchange.NSE,
    segment=Segment.EQUITY,
    security_id="2885",
    lot_size=1,
)


def _session() -> object:
    return session_for(date(2026, 10, 19), load_exchange_rules().session)


def _ist(hour: int, minute: int, second: int = 0) -> datetime:
    return datetime(2026, 10, 19, hour, minute, second, tzinfo=IST)


def _tick(hour: int, minute: int, second: int = 0, quantity: int = 10) -> Tick:
    return Tick(
        instrument=INSTRUMENT,
        price=Price.from_rupees(Decimal("100")),
        quantity=quantity,
        exchange_time=_ist(hour, minute, second),
    )


def _hypertables() -> tuple[bool, str]:
    try:
        engine = create_db_engine(load_infra_settings())
        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT hypertable_name FROM timescaledb_information.hypertables "
                    "ORDER BY hypertable_name"
                )
            ).all()
    except Exception as error:  # report it, do not crash the harness
        return False, f"{type(error).__name__}: {error}"

    found = {row[0] for row in rows}
    missing = EXPECTED_HYPERTABLES - found
    return not missing, f"{sorted(found)}" if not missing else f"missing {sorted(missing)}"


def _price_columns_are_bigint() -> tuple[bool, str]:
    try:
        engine = create_db_engine(load_infra_settings())
        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT table_name, column_name, data_type FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND column_name LIKE '%paise'"
                )
            ).all()
    except Exception as error:  # report it, do not crash the harness
        return False, f"{type(error).__name__}: {error}"

    actual = {(row[0], row[1]): row[2] for row in rows}
    wrong = []
    for table, columns in PRICE_COLUMNS.items():
        for column in columns:
            kind = actual.get((table, column))
            if kind != "bigint":
                wrong.append(f"{table}.{column}={kind}")

    return not wrong, "all bigint" if not wrong else ", ".join(wrong)


def _gaps_table_rejects_a_backwards_interval() -> tuple[bool, str]:
    """The check constraint must actually be enforced, not merely declared."""
    now = datetime.now(IST)
    try:
        engine = create_db_engine(load_infra_settings())
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO data_gaps (started_at, ended_at, reason, recorded_at) "
                    "VALUES (:late, :early, 'induced check', :now)"
                ),
                {"late": now, "early": now.replace(year=now.year - 1), "now": now},
            )
    except Exception as error:
        return "ck_data_gaps_ordered" in str(error), type(error).__name__
    return False, "a gap ending before it started was accepted"


def _no_bar_without_a_trade() -> tuple[bool, str]:
    series = build_bars([_tick(9, 15), _tick(9, 35)], 5, _session())  # type: ignore[arg-type]
    starts = [bar.start for bar in series.bars]
    correct = starts == [_ist(9, 15), _ist(9, 35)]
    return correct, f"{len(series.bars)} bars from 2 ticks 20 minutes apart"


def _boundary_ticks() -> tuple[bool, str]:
    session = _session()
    at_open = build_bars([_tick(9, 15)], 5, session)  # type: ignore[arg-type]
    last_second = build_bars([_tick(15, 29, 59)], 5, session)  # type: ignore[arg-type]
    at_close = build_bars([_tick(15, 30)], 5, session)  # type: ignore[arg-type]

    correct = (
        len(at_open.bars) == 1
        and at_open.bars[0].start == _ist(9, 15)
        and len(last_second.bars) == 1
        and last_second.bars[0].start == _ist(15, 25)
        and at_close.bars == ()
    )
    return correct, (
        f"open->{at_open.bars[0].start.strftime('%H:%M') if at_open.bars else 'none'}, "
        f"15:29:59->{last_second.bars[0].start.strftime('%H:%M') if last_second.bars else 'none'}, "
        f"15:30:00->{len(at_close.bars)} bars"
    )


def _late_tick_does_not_rewrite_a_closed_bar() -> tuple[bool, str]:
    series = build_bars(
        [_tick(9, 15, quantity=5), _tick(9, 25, quantity=7), _tick(9, 16, quantity=1000)],
        5,
        _session(),  # type: ignore[arg-type]
    )
    late = series.rejected_for(RejectReason.LATE)
    correct = series.volume == VOLUME_AFTER_DROPPING_A_LATE_TICK and len(late) == 1
    return correct, (
        f"volume {series.volume} (need {VOLUME_AFTER_DROPPING_A_LATE_TICK}), "
        f"{len(late)} late tick(s)"
    )


def _every_tick_is_accounted_for() -> tuple[bool, str]:
    ticks = [_tick(9, 15), _tick(9, 30), _tick(16, 0), _tick(9, 16)]
    series = build_bars(ticks, 5, _session())  # type: ignore[arg-type]
    in_bars = sum(1 for _ in series.bars)
    accounted = in_bars + len(series.rejected)
    # Two ticks merge into bars, so compare volume rather than counts.
    volume_in_bars = series.volume
    rejected_volume = sum(item.tick.quantity for item in series.rejected)
    total = volume_in_bars + rejected_volume
    return total == UNITS_IN_THE_ACCOUNTING_FIXTURE, (
        f"{total} of {UNITS_IN_THE_ACCOUNTING_FIXTURE} units in bars or rejected "
        f"({accounted} outcomes)"
    )


def run() -> list[CheckResult]:
    hypertables_ok, hypertables_detail = _hypertables()
    bigint_ok, bigint_detail = _price_columns_are_bigint()
    constraint_ok, constraint_detail = _gaps_table_rejects_a_backwards_interval()
    no_bar_ok, no_bar_detail = _no_bar_without_a_trade()
    boundary_ok, boundary_detail = _boundary_ticks()
    late_ok, late_detail = _late_tick_does_not_rewrite_a_closed_bar()
    accounted_ok, accounted_detail = _every_tick_is_accounted_for()
    session = _session()

    return [
        CheckResult(
            name="hypertables_exist",
            passed=hypertables_ok,
            measured=hypertables_detail,
            requirement=f"need {sorted(EXPECTED_HYPERTABLES)}",
        ),
        CheckResult(
            name="prices_are_bigint_paise",
            passed=bigint_ok,
            measured=bigint_detail,
            requirement="need bigint everywhere - never double precision",
        ),
        CheckResult(
            name="gap_interval_constraint_enforced",
            passed=constraint_ok,
            measured=constraint_detail,
            requirement="need the database to reject a gap ending before it starts",
        ),
        CheckResult(
            name="session_is_375_minutes",
            passed=session.minutes == SESSION_MINUTES,  # type: ignore[attr-defined]
            measured=f"{session.minutes} minutes",  # type: ignore[attr-defined]
            requirement=f"need {SESSION_MINUTES} (09:15-15:30 IST)",
        ),
        CheckResult(
            name="no_bar_is_invented_for_a_quiet_window",
            passed=no_bar_ok,
            measured=no_bar_detail,
            requirement="need 2 bars, not 5 with three flat fillers",
        ),
        CheckResult(
            name="boundary_ticks_land_correctly",
            passed=boundary_ok,
            measured=boundary_detail,
            requirement="need open->09:15, 15:29:59->15:25, 15:30:00 rejected",
        ),
        CheckResult(
            name="late_tick_cannot_rewrite_a_closed_bar",
            passed=late_ok,
            measured=late_detail,
            requirement="need the closed bar untouched and the tick reported",
        ),
        CheckResult(
            name="no_tick_disappears",
            passed=accounted_ok,
            measured=accounted_detail,
            requirement="need every tick in a bar or in rejected",
        ),
    ]
