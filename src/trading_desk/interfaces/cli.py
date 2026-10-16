"""The application entry point.

    uv run trading-desk            # authenticate, then run on schedule
    uv run trading-desk snapshot   # authenticate, capture once, exit
    uv run trading-desk login      # re-authenticate deliberately

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
from trading_desk.application.calendars import load_trading_calendar
from trading_desk.application.snapshotter import ChainSnapshotter
from trading_desk.application.universe import load_universe_request, resolve_universe
from trading_desk.infra.config import load_app_settings, load_dhan_settings
from trading_desk.infra.logging import configure_logging, register_secret
from trading_desk.ports.notifier import AlertLevel

# Three captures a session. Open, midday and just before close, so the IV series
# has a shape rather than a single daily point. A full run takes about four
# minutes at Dhan's rate limit, hence 15:15 rather than closer to 15:30.
SNAPSHOT_TIMES_IST = [(9, 30), (12, 30), (15, 15)]

log = logging.getLogger("trading_desk.app")


def _build_snapshotter(client: DhanClient, clock: SystemClock) -> ChainSnapshotter:
    return ChainSnapshotter(
        chain_source=DhanOptionChainAdapter(client, clock),
        archive=ParquetChainArchive(),
        notifier=LoggingNotifier(clock),
        clock=clock,
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

        health = _build_snapshotter(client, clock).run(list(universe.all_instruments()))
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
    if command == "run":
        return _start_scheduler()

    print(f"Unknown command: {command}", file=sys.stderr)
    print("Usage: trading-desk [run|snapshot|login]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
