"""The application entry point.

    uv run trading-desk            # authenticate, then run on schedule
    uv run trading-desk snapshot   # authenticate, capture once, exit
    uv run trading-desk login      # re-authenticate deliberately
    uv run trading-desk survey     # measure every F&O chain, rebuild the universe

Starting the app authenticates it. A browser opens only when there is no usable
token, so the first run of the day may ask for a login and every run after that
goes straight to work.

This is the one place that wires concrete adapters into the application - which
is how the same snapshotter runs against fakes in tests and Dhan in production.

Operational output goes through the logger, never `print`. The logger writes to
stderr, which Python keeps line-buffered even when redirected; stdout is
block-buffered under a redirect, so `print` output from a long-running scheduler
sits in an unflushed buffer for hours. Found exactly that way: `trading-desk run
> app.log` logged the scheduler lines and lost every `print`.
"""

from __future__ import annotations

import logging
import sys

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from trading_desk.adapters.clock import IST, SystemClock
from trading_desk.adapters.dhan.chain import DhanOptionChainAdapter
from trading_desk.adapters.dhan.client import DhanClient
from trading_desk.adapters.dhan.instruments import DhanInstrumentMaster
from trading_desk.adapters.dhan.login import ensure_valid_token, hours_remaining
from trading_desk.adapters.dhan.token_store import StoredTokenProvider
from trading_desk.adapters.notify.logging_notifier import LoggingNotifier
from trading_desk.adapters.store.parquet_chain import ParquetChainArchive
from trading_desk.application.calendars import TradingCalendar, load_trading_calendar
from trading_desk.application.exchange_rules import load_exchange_rules
from trading_desk.application.liquidity_survey import LiquiditySurvey
from trading_desk.application.snapshotter import ChainSnapshotter
from trading_desk.application.universe import load_universe_request, resolve_universe
from trading_desk.application.universe_builder import (
    DEFAULT_GENERATED_PATH,
    diff_universe,
    previous_symbols,
    previous_version,
    render_universe,
)
from trading_desk.domain.liquidity import rank_by_liquidity
from trading_desk.infra.config import load_app_settings, load_dhan_settings
from trading_desk.infra.logging import configure_logging, register_secret
from trading_desk.ports.notifier import AlertLevel

# Three captures a session. Open, midday and just before close, so the IV series
# has a shape rather than a single daily point. A full run takes about four
# minutes at Dhan's rate limit, hence 15:15 rather than closer to 15:30.
SNAPSHOT_TIMES_IST = [(9, 30), (12, 30), (15, 15)]

log = logging.getLogger("trading_desk.app")


def _build_snapshotter(
    client: DhanClient, clock: SystemClock, calendar: TradingCalendar
) -> ChainSnapshotter:
    return ChainSnapshotter(
        chain_source=DhanOptionChainAdapter(client, clock),
        archive=ParquetChainArchive(),
        notifier=LoggingNotifier(clock),
        clock=clock,
        calendar=calendar,
    )


def _run_capture() -> int:
    """One capture run. Re-authenticates if the token has aged out since startup."""
    clock = SystemClock()
    settings = load_dhan_settings()
    calendar = load_trading_calendar()
    today = clock.utc_now().astimezone(IST).date()

    closed = calendar.reason_closed(today)
    if closed is not None:
        log.info("%s: market closed (%s) - skipping capture", today, closed)
        return 0

    ensure_valid_token(settings)
    client = DhanClient(client_id=settings.client_id, token_provider=StoredTokenProvider())

    try:
        catalogue = DhanInstrumentMaster().option_underlyings(today)
        universe = resolve_universe(load_universe_request(), catalogue)
        if universe.unavailable:
            log.warning("No listed options, skipping: %s", ", ".join(universe.unavailable))

        snapshotter = _build_snapshotter(client, clock, calendar)
        health = snapshotter.run(list(universe.all_instruments()))
    finally:
        client.close()

    log.info("%s", health.summary())
    for failure in health.failed:
        log.error("capture failed for %s: %s", failure.symbol, failure.error)
    return 0 if health.is_complete else 1


def _start_scheduler() -> int:
    """Authenticate once, then capture on schedule until stopped."""
    clock = SystemClock()
    settings = load_dhan_settings()
    calendar = load_trading_calendar()

    grant = ensure_valid_token(settings)
    log.info("Authenticated. Token valid for %.1f hours.", hours_remaining(grant))

    if not calendar.verified:
        LoggingNotifier(clock).send_alert(
            AlertLevel.WARNING,
            "Holiday calendar is unverified - check config/holidays.yaml against the NSE circular",
        )

    scheduler = BlockingScheduler(timezone=IST)
    for hour, minute in SNAPSHOT_TIMES_IST:
        scheduler.add_job(
            _run_capture,
            CronTrigger(day_of_week="mon-fri", hour=hour, minute=minute, timezone=IST),
            id=f"snapshot_{hour:02d}{minute:02d}",
            misfire_grace_time=600,  # a laptop waking late should still capture
            coalesce=True,  # one run, not a backlog, after a long sleep
        )

    times = ", ".join(f"{hour:02d}:{minute:02d}" for hour, minute in SNAPSHOT_TIMES_IST)
    log.info("Scheduled captures (IST, Mon-Fri, skipping holidays): %s", times)
    log.info("Running. Ctrl-C to stop.")

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("Stopped.")
    return 0


def _run_survey() -> int:
    """Measure every F&O chain, rank it, and rewrite config/universe.yaml.

    Takes about half an hour: ~218 underlyings, two rate-limited calls each.

    Refuses to run on a non-trading day. Volume is session-cumulative and Dhan goes
    on serving the previous session's figures when the market is shut, so a weekend
    survey silently measures Friday and nothing in the response reveals it. The
    trading calendar is the only thing that knows, so it is checked here, before
    half an hour of requests. `--force` overrides it and the measured date is still
    recorded in the file.
    """
    clock = SystemClock()
    settings = load_dhan_settings()
    rules = load_exchange_rules()
    calendar = load_trading_calendar()
    today = clock.utc_now().astimezone(IST).date()

    closed = calendar.reason_closed(today)
    if closed is not None and "--force" not in sys.argv:
        log.error(
            "%s: market closed (%s). A survey now would measure the previous session's "
            "volume without saying so. Re-run on a trading day, or pass --force.",
            today,
            closed,
        )
        return 1

    if not rules.verified:
        log.warning(
            "Liquidity thresholds are unreviewed (%s) - check config/exchange_rules.yaml",
            rules.source,
        )

    ensure_valid_token(settings)
    client = DhanClient(client_id=settings.client_id, token_provider=StoredTokenProvider())

    try:
        catalogue = DhanInstrumentMaster().option_underlyings(today)
        log.info("Surveying %d underlyings. This takes about half an hour.", len(catalogue))

        survey = LiquiditySurvey(
            chain_source=DhanOptionChainAdapter(client, clock),
            notifier=LoggingNotifier(clock),
            clock=clock,
        )
        result = survey.run(catalogue)
    finally:
        client.close()

    log.info("%s", result.summary())
    for failure in result.failures:
        log.warning("not measured: %s - %s", failure.symbol, failure.error)

    unusable = result.unusable_reason
    if unusable is not None:
        log.error("Survey unusable, universe left unchanged: %s", unusable)
        return 1

    ranking = rank_by_liquidity(tuple(result.measures), rules.liquidity)
    diff = diff_universe(previous_symbols(DEFAULT_GENERATED_PATH), ranking.symbols)

    document = render_universe(
        ranking=ranking,
        rules=rules,
        as_of=today,
        version=previous_version(DEFAULT_GENERATED_PATH) + 1,
        window_days=1,  # a point measurement; the plan's 20-day average needs history
        measured_on=today,
    )
    DEFAULT_GENERATED_PATH.write_text(document)

    log.info("Wrote %s with %d symbols", DEFAULT_GENERATED_PATH, len(ranking.included))
    log.info("Diff against the previous universe: %s", diff.summary())
    return 0


def main() -> int:
    command = sys.argv[1] if len(sys.argv) > 1 else "run"

    settings = load_dhan_settings()
    register_secret(settings.api_secret)
    configure_logging(load_app_settings().log_level)

    if command == "login":
        grant = ensure_valid_token(settings, force="--force" in sys.argv)
        log.info("Authenticated. Token valid for %.1f hours.", hours_remaining(grant))
        return 0
    if command == "snapshot":
        return _run_capture()
    if command == "survey":
        return _run_survey()
    if command == "run":
        return _start_scheduler()

    print(f"Unknown command: {command}", file=sys.stderr)
    print("Usage: trading-desk [run|snapshot|login|survey]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
