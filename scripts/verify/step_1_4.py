"""Verify step 1.4 - historical backfill.

Runs a real backfill twice against Dhan and inspects the database. The second run
is the point: a loader that duplicates on re-run is the most common data bug
there is, and the only symptom is counts that are merely too large.

Also cross-checks the daily close against a second, unrelated Dhan endpoint. Two
endpoints agreeing is evidence the mapping is right; a fixture can only prove we
parse what we think they send.
"""

from __future__ import annotations

from datetime import date

from scripts.verify._runner import CheckResult
from trading_desk.adapters.clock import SystemClock
from trading_desk.adapters.dhan.chain import DhanOptionChainAdapter
from trading_desk.adapters.dhan.client import DhanClient
from trading_desk.adapters.dhan.history import DhanHistoryAdapter
from trading_desk.adapters.dhan.token_store import StoredTokenProvider, load_token
from trading_desk.adapters.notify.logging_notifier import LoggingNotifier
from trading_desk.adapters.store.bar_repository import PostgresBarStore
from trading_desk.application.backfill import BackfillService
from trading_desk.domain.enums import Exchange, Segment
from trading_desk.domain.instruments import InstrumentRef
from trading_desk.infra.config import load_dhan_settings, load_infra_settings
from trading_desk.infra.db import create_db_engine, create_session_factory

TITLE = "Historical backfill"

BACKFILL_START = date(2023, 1, 1)
BACKFILL_END = date(2026, 10, 17)

# Two years of Indian trading days is roughly 250 a year. Far below this and the
# range request silently returned a truncated window.
MIN_DAILY_BARS = 450

# An index ATR above this would mean the paise conversion is off by a factor.
MAX_PLAUSIBLE_INDEX_ATR_PAISE = 500_00

NIFTY = InstrumentRef(
    symbol="NIFTY",
    exchange=Exchange.NSE,
    segment=Segment.EQUITY,  # the index itself; the adapter maps it to IDX_I by symbol
    security_id="13",
    lot_size=65,
)
RELIANCE = InstrumentRef(
    symbol="RELIANCE",
    exchange=Exchange.NSE,
    segment=Segment.EQUITY,
    security_id="2885",
    lot_size=1,
)
INSTRUMENTS = [NIFTY, RELIANCE]


def _stored_token_exists() -> bool:
    try:
        load_token()
    except (FileNotFoundError, KeyError, ValueError):
        return False
    return True


def _run_backfill_twice() -> tuple[object, object, object, str]:
    """Returns (first report, second report, store, error)."""
    if not _stored_token_exists():
        return None, None, None, "no stored Dhan token - run: make login"

    clock = SystemClock()
    settings = load_dhan_settings()
    client = DhanClient(client_id=settings.client_id, token_provider=StoredTokenProvider())
    store = PostgresBarStore(create_session_factory(create_db_engine(load_infra_settings())))
    service = BackfillService(DhanHistoryAdapter(client), store, LoggingNotifier(clock))

    try:
        first = service.run(INSTRUMENTS, BACKFILL_START, BACKFILL_END)
        second = service.run(INSTRUMENTS, BACKFILL_START, BACKFILL_END)
    finally:
        client.close()

    return first, second, store, ""


def _close_matches_the_chain_spot() -> tuple[bool, str]:
    """The latest daily close must equal the option chain's spot for the same day.

    An independent cross-check: /charts/historical and /optionchain are different
    endpoints with different response shapes. If the price mapping were wrong -
    a float conversion, a paise factor - they would disagree.
    """
    if not _stored_token_exists():
        return False, "no stored Dhan token"

    clock = SystemClock()
    settings = load_dhan_settings()
    client = DhanClient(client_id=settings.client_id, token_provider=StoredTokenProvider())
    try:
        bars = DhanHistoryAdapter(client).daily_bars(NIFTY, BACKFILL_START, BACKFILL_END)
        chains = DhanOptionChainAdapter(client, clock)
        expiries = chains.available_expiries(NIFTY)
        snapshot = chains.fetch_chain(NIFTY, expiries[0])
    except Exception as error:  # report it, do not crash the harness
        return False, f"{type(error).__name__}: {str(error)[:100]}"
    finally:
        client.close()

    if not bars:
        return False, "no daily bars"

    latest_close = bars[-1].close
    matches = latest_close == snapshot.spot
    return matches, f"daily close {latest_close} vs chain spot {snapshot.spot}"


def run() -> list[CheckResult]:
    first, second, store, error = _run_backfill_twice()

    if first is None or second is None or store is None:
        return [CheckResult("backfill_runs", False, error, "need a stored token")]

    cross_ok, cross_detail = _close_matches_the_chain_spot()

    nifty_dates = store.daily_bar_dates("NIFTY")  # type: ignore[attr-defined]
    unique_dates = len(set(nifty_dates)) == len(nifty_dates)
    ready = [item for item in first.results if item.indicators_ready]  # type: ignore[attr-defined]
    atr = next((item.atr for item in first.results if item.symbol == "NIFTY"), None)  # type: ignore[attr-defined]

    return [
        CheckResult(
            name="backfill_completes",
            passed=not first.failed,  # type: ignore[attr-defined]
            measured=first.summary(),  # type: ignore[attr-defined]
            requirement="need every instrument loaded",
        ),
        CheckResult(
            name="rerun_inserts_nothing",
            passed=second.bars_inserted == 0,  # type: ignore[attr-defined]
            measured=f"{second.bars_inserted} inserted on the second run",  # type: ignore[attr-defined]
            requirement="need 0 - the loader must be idempotent",
        ),
        CheckResult(
            name="no_duplicate_trading_days",
            passed=unique_dates,
            measured=f"{len(nifty_dates)} rows, {len(set(nifty_dates))} distinct dates",
            requirement="need every stored date to appear once",
        ),
        CheckResult(
            name="two_years_of_history",
            passed=len(nifty_dates) >= MIN_DAILY_BARS,
            measured=(
                f"{len(nifty_dates)} bars, {nifty_dates[0]}..{nifty_dates[-1]}"
                if nifty_dates
                else "none"
            ),
            requirement=f"need >= {MIN_DAILY_BARS} daily bars",
        ),
        CheckResult(
            name="indicators_computable_for_every_name",
            passed=len(ready) == len(INSTRUMENTS),
            measured=f"{len(ready)}/{len(INSTRUMENTS)} with ATR and a 20-day range",
            requirement="need every universe name ready - the done-when of 1.4",
        ),
        CheckResult(
            name="atr_is_plausible",
            passed=atr is not None and 0 < atr.paise < MAX_PLAUSIBLE_INDEX_ATR_PAISE,
            measured=f"NIFTY ATR(14) {atr}" if atr else "none",
            requirement="need 0 < ATR < Rs 500 on an index near 23,000",
        ),
        CheckResult(
            name="close_matches_an_independent_endpoint",
            passed=cross_ok,
            measured=cross_detail,
            requirement="need /charts/historical and /optionchain to agree",
        ),
        CheckResult(
            name="suspect_moves_reported_not_adjusted",
            passed=True,
            measured=f"{len(first.suspect_moves)} move(s) flagged for explanation",  # type: ignore[attr-defined]
            requirement="informational - large moves are flagged, never corrected",
        ),
    ]
