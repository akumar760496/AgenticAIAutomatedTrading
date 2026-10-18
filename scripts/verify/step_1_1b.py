"""Verify step 1.1b - chain archive and snapshotter.

Runs a real capture against Dhan and inspects what landed on disk. The offline
tests prove the shape; only this proves the archive is actually accumulating the
series the project depends on.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

from scripts.verify._runner import CheckResult
from trading_desk.adapters.clock import SystemClock, to_ist
from trading_desk.adapters.dhan.chain import DhanOptionChainAdapter
from trading_desk.adapters.dhan.client import DhanClient
from trading_desk.adapters.dhan.instruments import DhanInstrumentMaster
from trading_desk.adapters.dhan.token_store import StoredTokenProvider, load_token
from trading_desk.adapters.notify.logging_notifier import LoggingNotifier
from trading_desk.adapters.store.parquet_chain import SCHEMA, ParquetChainArchive
from trading_desk.application.calendars import load_trading_calendar
from trading_desk.application.snapshotter import ChainSnapshotter
from trading_desk.application.universe import load_universe_request, resolve_universe
from trading_desk.infra.config import load_dhan_settings

TITLE = "Chain archive and snapshotter"

ROOT = Path(__file__).resolve().parents[2]
ARCHIVE_ROOT = ROOT / "data" / "chains"

MIN_ROWS_PER_INSTRUMENT = 100

# Plausibility is checked on ATM strikes, not across the whole chain. A chain-wide
# bound fails on data that is correct-as-captured: deep ITM strikes that did not
# trade carry a stale `last` of several times intrinsic value, and Dhan's solver
# backs 350-420% IV out of it. Measured 2026-10-17: 49 of 4,942 rows above 200%,
# every one with volume 0, while ATM IV ranged 6.2-35.5%. ATM is what the IV-rank
# series is built from, so that is what must be sane. See ISS-007.
MAX_PLAUSIBLE_ATM_IV = 100.0

# Above this an IV is a stale-print artefact rather than a quote. Reported, not
# failed: the rows are a faithful record of what the exchange published.
STALE_PRINT_IV = 200.0


def _stored_token_exists() -> bool:
    """A usable consent-flow grant on disk, the same one the app uses."""
    try:
        load_token()
    except (FileNotFoundError, KeyError, ValueError):
        return False
    return True


def _live_capture() -> tuple[bool, str]:
    """One real snapshot run of the whole universe."""
    settings = load_dhan_settings()
    if not _stored_token_exists():
        return False, "no stored Dhan token - run: make login"

    clock = SystemClock()
    client = DhanClient(
        client_id=settings.client_id,
        token_provider=StoredTokenProvider(),
    )
    calendar = load_trading_calendar()
    closed = calendar.reason_closed(to_ist(clock.utc_now()).date())
    if closed is not None:
        # Checked before authenticating, so an out-of-hours verify run costs nothing.
        # The snapshotter would refuse anyway; this just gives a readable reason.
        return False, f"market closed ({closed}) - a live capture would archive stale figures"

    snapshotter = ChainSnapshotter(
        chain_source=DhanOptionChainAdapter(client, clock),
        archive=ParquetChainArchive(ARCHIVE_ROOT),
        notifier=LoggingNotifier(clock),
        clock=clock,
        calendar=calendar,
    )
    try:
        catalogue = DhanInstrumentMaster().option_underlyings(clock.utc_now().date())
        universe = resolve_universe(load_universe_request(), catalogue)
        health = snapshotter.run(list(universe.all_instruments()))
    finally:
        client.close()

    return health.is_complete, health.summary()


def _max_float(series: pl.Series) -> float:
    """polars types max() as a wide union; narrow it rather than cast blindly."""
    raw = series.max()
    return float(raw) if isinstance(raw, int | float) else 0.0


def _archive_frame() -> pl.DataFrame | None:
    files = sorted(ARCHIVE_ROOT.rglob("*.parquet"))
    if not files:
        return None
    return pl.concat([pl.read_parquet(path) for path in files])


def run() -> list[CheckResult]:
    capture_ok, capture_detail = _live_capture()
    frame = _archive_frame()

    if frame is None:
        empty = "no parquet files written"
        return [
            CheckResult("live_capture", capture_ok, capture_detail, "need a complete run"),
            CheckResult("archive_written", False, empty, "need at least one file"),
        ]

    today_ist = to_ist(SystemClock().utc_now()).date().isoformat()
    partitions = [path.name for path in ARCHIVE_ROOT.glob("date=*") if path.is_dir()]
    schema_matches = dict(frame.schema) == SCHEMA

    priced = frame.filter(pl.col("implied_volatility").is_not_null())
    unpriced = frame.height - priced.height
    zero_ivs = priced.filter(pl.col("implied_volatility") == 0.0).height

    # One row per (underlying, expiry, snapshot): the strike nearest spot.
    atm = (
        priced.with_columns(distance=(pl.col("strike_paise") - pl.col("spot_paise")).abs())
        .sort("distance")
        .group_by(["underlying", "expiry", "received_at"])
        .head(2)
    )
    atm_max = _max_float(atm["implied_volatility"]) if atm.height else 0.0
    stale_prints = priced.filter(pl.col("implied_volatility") > STALE_PRINT_IV)
    stale_all_untraded = stale_prints.height == 0 or bool((stale_prints["volume"] == 0).all())

    return [
        CheckResult(
            name="live_capture",
            passed=capture_ok,
            measured=capture_detail,
            requirement="need every instrument captured",
        ),
        CheckResult(
            name="rows_written",
            passed=frame.height >= MIN_ROWS_PER_INSTRUMENT,
            measured=str(frame.height),
            requirement=f"need >= {MIN_ROWS_PER_INSTRUMENT}",
        ),
        CheckResult(
            name="partitioned_by_ist_date",
            passed=bool(partitions) and (capture_ok or f"date={today_ist}" not in partitions),
            measured=f"{sorted(set(partitions))}, today={today_ist}",
            requirement=(
                f"need date={today_ist} after a live capture; on a closed day, need no "
                f"partition for today"
            ),
        ),
        CheckResult(
            name="schema_is_fixed",
            passed=schema_matches,
            measured="matches" if schema_matches else "differs from SCHEMA",
            requirement="need identical columns in every file",
        ),
        CheckResult(
            name="unpriced_strikes_are_null",
            passed=unpriced > 0 and zero_ivs == 0,
            measured=f"{unpriced} null, {zero_ivs} stored as 0.0",
            requirement="need some null and zero stored as 0.0",
        ),
        CheckResult(
            name="atm_iv_is_plausible",
            passed=0.0 < atm_max < MAX_PLAUSIBLE_ATM_IV,
            measured=f"max ATM IV {atm_max:.2f} over {atm.height} strikes",
            requirement=f"need 0 < max < {MAX_PLAUSIBLE_ATM_IV}",
        ),
        CheckResult(
            name="stale_prints_are_confined_to_untraded_strikes",
            passed=stale_all_untraded,
            measured=f"{stale_prints.height} rows above {STALE_PRINT_IV:.0f}% IV, all volume 0"
            if stale_all_untraded
            else f"{stale_prints.height} rows above {STALE_PRINT_IV:.0f}% IV, some traded",
            requirement="need every implausible IV to sit on a strike that did not trade",
        ),
        CheckResult(
            name="prices_are_integer_paise",
            passed=frame.schema["strike_paise"] == pl.Int64,
            measured=str(frame.schema["strike_paise"]),
            requirement="need Int64, never a float",
        ),
    ]
