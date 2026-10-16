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
from trading_desk.adapters.dhan.token import StaticTokenProvider
from trading_desk.adapters.notify.logging_notifier import LoggingNotifier
from trading_desk.adapters.store.parquet_chain import SCHEMA, ParquetChainArchive
from trading_desk.application.snapshotter import ChainSnapshotter
from trading_desk.application.universe import load_universe_request, resolve_universe
from trading_desk.infra.config import load_dhan_settings

TITLE = "Chain archive and snapshotter"

ROOT = Path(__file__).resolve().parents[2]
ARCHIVE_ROOT = ROOT / "data" / "chains"

MIN_ROWS_PER_INSTRUMENT = 100
MAX_PLAUSIBLE_IV = 200.0


def _live_capture() -> tuple[bool, str]:
    """One real snapshot run of the whole universe."""
    settings = load_dhan_settings()
    if settings.access_token is None or not settings.access_token.get_secret_value():
        return False, "no DHAN_ACCESS_TOKEN in .env"

    clock = SystemClock()
    client = DhanClient(
        client_id=settings.client_id,
        token_provider=StaticTokenProvider(settings.access_token.get_secret_value()),
    )
    snapshotter = ChainSnapshotter(
        chain_source=DhanOptionChainAdapter(client, clock),
        archive=ParquetChainArchive(ARCHIVE_ROOT),
        notifier=LoggingNotifier(clock),
        clock=clock,
    )
    try:
        catalogue = DhanInstrumentMaster().option_underlyings(clock.utc_now().date())
        universe = resolve_universe(load_universe_request(), catalogue)
        health = snapshotter.run(list(universe.all_instruments()))
    finally:
        client.close()

    return health.is_complete, health.summary()


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
    # polars types max() as a wide union; narrow it rather than cast blindly.
    raw_max = priced["implied_volatility"].max() if priced.height else None
    iv_max = float(raw_max) if isinstance(raw_max, int | float) else 0.0
    zero_ivs = priced.filter(pl.col("implied_volatility") == 0.0).height

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
            passed=f"date={today_ist}" in partitions,
            measured=str(sorted(set(partitions))),
            requirement=f"need date={today_ist}",
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
            name="iv_values_are_plausible",
            passed=0.0 < iv_max < MAX_PLAUSIBLE_IV,
            measured=f"max IV {iv_max:.2f}",
            requirement=f"need 0 < max < {MAX_PLAUSIBLE_IV}",
        ),
        CheckResult(
            name="prices_are_integer_paise",
            passed=frame.schema["strike_paise"] == pl.Int64,
            measured=str(frame.schema["strike_paise"]),
            requirement="need Int64, never a float",
        ),
    ]
