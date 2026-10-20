"""The application entry point.

    uv run trading-desk            # authenticate, then run on schedule
    uv run trading-desk snapshot   # authenticate, capture once, exit
    uv run trading-desk login      # re-authenticate deliberately
    uv run trading-desk survey     # measure every F&O chain, rebuild the universe
    uv run trading-desk brief      # run one research cycle and send the briefing
    uv run trading-desk capture    # record decisions waiting in Telegram
    uv run trading-desk nag        # chase unanswered ideas (after 10:00 IST)
    uv run trading-desk review     # where every open idea stands at the close
    uv run trading-desk telegram-check   # prove delivery before 08:15 depends on it

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
from collections.abc import Callable
from pathlib import Path

from trading_desk.adapters.backup.postgres_dump import PostgresBackupTool
from trading_desk.adapters.clock import IST, SystemClock
from trading_desk.adapters.dhan.chain import DhanOptionChainAdapter
from trading_desk.adapters.dhan.client import DhanClient
from trading_desk.adapters.dhan.instruments import DhanInstrumentMaster
from trading_desk.adapters.dhan.login import ensure_valid_token, hours_remaining
from trading_desk.adapters.dhan.token_store import StoredTokenProvider
from trading_desk.adapters.notify.logging_notifier import LoggingNotifier
from trading_desk.adapters.store.bar_repository import PostgresBarStore
from trading_desk.adapters.store.expiry_repository import PostgresExpiryStore
from trading_desk.adapters.store.jsonl_job_log import JsonlJobLog
from trading_desk.adapters.store.news_repository import PostgresNewsStore
from trading_desk.adapters.store.parquet_chain import ParquetChainArchive
from trading_desk.adapters.telegram.client import TelegramClient
from trading_desk.adapters.telegram.notifier import TelegramNotifier
from trading_desk.application.backtest import (
    DEFAULT_RESULTS_DIR,
    BacktestRunner,
    StrategyResult,
)
from trading_desk.application.backup import BackupService
from trading_desk.application.calendars import TradingCalendar, load_trading_calendar
from trading_desk.application.exchange_rules import load_exchange_rules
from trading_desk.application.expiry_refresh import ExpiryRefresh
from trading_desk.application.label_set import (
    DEFAULT_LABEL_PATH,
    DEFAULT_SAMPLE_SIZE,
    VALID_LABELS,
    LabelFileError,
    write_sample,
)
from trading_desk.application.liquidity_survey import LiquiditySurvey
from trading_desk.application.scheduler import (
    BACKUP,
    BRIEF,
    CALIBRATION,
    CAPTURE,
    CAPTURE_INTERVAL_SECONDS,
    EXPIRY_REFRESH,
    HEALTH,
    NAG,
    REVIEW,
    SCORE,
    SNAPSHOT,
    DailyJob,
    JobGuard,
    PollingJob,
    build_scheduler,
)
from trading_desk.application.snapshotter import ChainSnapshotter
from trading_desk.application.universe import load_universe_request, resolve_universe
from trading_desk.application.universe_builder import (
    DEFAULT_GENERATED_PATH,
    diff_universe,
    previous_symbols,
    previous_version,
    render_universe,
)
from trading_desk.domain.backtest.trade import SimulatedTrade
from trading_desk.domain.liquidity import rank_by_liquidity
from trading_desk.domain.news import filing_as_article
from trading_desk.infra.config import (
    load_app_settings,
    load_dhan_settings,
    load_infra_settings,
    load_telegram_settings,
)
from trading_desk.infra.db import create_db_engine, create_session_factory
from trading_desk.infra.logging import configure_logging, register_secret
from trading_desk.interfaces.briefing_cli import (
    run_briefing,
    run_calibration,
    run_capture_once,
    run_health,
    run_nag,
    run_review,
    run_scoring,
    run_telegram_check,
)
from trading_desk.interfaces.dashboard import DASHBOARD_PORT, serve_dashboard
from trading_desk.ports.notifier import AlertLevel

# Three captures a session. Open, midday and just before close, so the IV series
# has a shape rather than a single daily point. A full run takes about four
# minutes at Dhan's rate limit, hence 15:15 rather than closer to 15:30.
SNAPSHOT_TIMES_IST = [(9, 30), (12, 30), (15, 15)]

# The nightly backup, as `scripts/backup.py` spells it.
BACKUP_DATABASE = "trading_desk"
CHAIN_ARCHIVE_ROOT = Path("data/chains")

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


def _daily_jobs() -> list[DailyJob]:
    """The timetable, IST. One place, so it can be read in ten seconds.

    Ordered by time of day rather than by importance, because that is the
    order it will be debugged in.
    """
    jobs = [
        DailyJob(name=BRIEF, hour=8, minute=15, run=_job(run_briefing)),
        DailyJob(name=NAG, hour=10, minute=0, run=_job(run_nag)),
        DailyJob(name=REVIEW, hour=15, minute=45, run=_job(run_review)),
        # Scoring after the review, health after scoring. The order is the
        # dependency: the review reports, scoring writes the outcome rows, and
        # the no-trade rate is measured once the day's result is in the journal.
        DailyJob(name=SCORE, hour=16, minute=0, run=_job(run_scoring)),
        DailyJob(name=HEALTH, hour=16, minute=15, run=_job(run_health)),
        # Nightly, and not trading-day gated: the expiry list and the backup
        # are about our own state, not the market's.
        DailyJob(
            name=EXPIRY_REFRESH,
            hour=20,
            minute=0,
            run=_job(_refresh_expiries),
            trading_days_only=False,
        ),
        DailyJob(name=BACKUP, hour=23, minute=30, run=_job(_take_backup), trading_days_only=False),
        # Calibration is cheap and reads the whole history, so it runs daily
        # rather than monthly as FR-7.7 allows. A monthly report that nobody
        # has seen for four weeks is how a broken Brier score survives.
        DailyJob(
            name=CALIBRATION,
            hour=21,
            minute=0,
            run=_job(run_calibration),
            trading_days_only=False,
        ),
    ]
    for hour, minute in SNAPSHOT_TIMES_IST:
        jobs.append(
            DailyJob(
                name=f"{SNAPSHOT}_{hour:02d}{minute:02d}",
                hour=hour,
                minute=minute,
                run=_job(_run_capture),
            )
        )
    return jobs


def _job(command: Callable[[], int]) -> Callable[[], None]:
    """A command as a job.

    Every command returns an exit code; the guard reacts to exceptions. A
    non-zero code means the work ran and found something wrong - a partial
    capture, say - and that has already been logged and alerted by the command
    itself. Raising here would double-report it as a scheduler failure and
    break the unattended-session streak over a single missing chain.
    """

    def run() -> None:
        code = command()
        if code != 0:
            log.warning("job returned exit code %d", code)

    return run


def _refresh_expiries() -> int:
    """Re-read every underlying's expiry list and audit the holiday calendar.

    Nightly rather than per-capture because an expiry list changes when the
    exchange publishes a circular, not when a price moves. The audit is the
    valuable half: a Tuesday the exchange skipped is primary evidence of a
    holiday our own `config/holidays.yaml` does not list, and that is how
    2026-11-24 was found (step 1.8).
    """
    clock = SystemClock()
    settings = load_dhan_settings()
    calendar = load_trading_calendar()
    today = clock.utc_now().astimezone(IST).date()

    ensure_valid_token(settings)
    client = DhanClient(client_id=settings.client_id, token_provider=StoredTokenProvider())
    factory = create_session_factory(create_db_engine(load_infra_settings()))

    try:
        catalogue = DhanInstrumentMaster().option_underlyings(today)
        universe = resolve_universe(load_universe_request(), catalogue)
        report = ExpiryRefresh(
            chain_source=DhanOptionChainAdapter(client, clock),
            store=PostgresExpiryStore(factory),
            notifier=LoggingNotifier(clock),
            clock=clock,
        ).run(list(universe.all_instruments()), calendar)
    finally:
        client.close()

    log.info("%s", report.summary())
    return 0 if not report.failed else 1


def _take_backup() -> int:
    """The nightly dump plus a chain-archive digest. Same code as `make backup`."""
    clock = SystemClock()
    report = BackupService(
        tool=PostgresBackupTool(user=BACKUP_DATABASE),
        clock=clock,
        notifier=LoggingNotifier(clock),
        database=BACKUP_DATABASE,
        archive_root=CHAIN_ARCHIVE_ROOT,
    ).run()

    log.info("%s", report.summary())
    return 0


def _start_scheduler() -> int:
    """Authenticate once, then run the whole timetable until stopped."""
    clock = SystemClock()
    settings = load_dhan_settings()
    calendar = load_trading_calendar()
    register_secret(load_telegram_settings().bot_token)

    grant = ensure_valid_token(settings)
    log.info("Authenticated. Token valid for %.1f hours.", hours_remaining(grant))

    if not calendar.verified:
        LoggingNotifier(clock).send_alert(
            AlertLevel.WARNING,
            "Holiday calendar is unverified - check config/holidays.yaml against the NSE circular",
        )

    # The alerting notifier is Telegram, because an unattended scheduler whose
    # failures only reach a log file on a sleeping laptop is not alerting.
    telegram = TelegramClient(
        bot_token=load_telegram_settings().bot_token.get_secret_value(),
        chat_id=load_telegram_settings().chat_id,
    )
    guard = JobGuard(
        calendar=calendar,
        clock=clock,
        notifier=TelegramNotifier(telegram, clock),
        job_log=JsonlJobLog(),
    )

    daily = _daily_jobs()
    scheduler = build_scheduler(
        guard=guard,
        daily=daily,
        polling=[
            PollingJob(
                name=CAPTURE,
                every_seconds=CAPTURE_INTERVAL_SECONDS,
                run=_job(run_capture_once),
                # Not trading-day gated: a briefing sent on Friday is often
                # answered on Saturday, and a tap nobody collects is a
                # decision lost.
                trading_days_only=False,
            )
        ],
    )

    for job in sorted(daily, key=lambda item: (item.hour, item.minute)):
        log.info("  %02d:%02d IST  %s", job.hour, job.minute, job.name)
    log.info("  every %ds  %s", CAPTURE_INTERVAL_SECONDS, CAPTURE)
    log.info("Running. Ctrl-C to stop.")

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("Stopped.")
    finally:
        telegram.close()
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


def _write_label_sheet() -> int:
    """Draw a sample from the news archive for hand-labelling (step 3.2).

    Reads nothing from Dhan, so it is dispatched before the broker settings are
    loaded - a labelling command that needs trading credentials is a labelling
    command that cannot be run on a laptop without them.
    """
    size = DEFAULT_SAMPLE_SIZE
    for index, argument in enumerate(sys.argv):
        if argument == "--count" and index + 1 < len(sys.argv):
            size = int(sys.argv[index + 1])

    store = PostgresNewsStore(create_session_factory(create_db_engine(load_infra_settings())))
    # Over-draw then trim, so the sample is taken from the widest slice of
    # history available rather than the first N rows of it.
    #
    # Filings as well as articles. Exchange filings are the primary catalyst
    # source - an earnings beat or an order win arrives as a filing first and
    # as journalism afterwards - and they are the bulk of what the archive
    # actually holds. Labelling only the RSS articles would train and measure
    # the classifier on the thinner half of its real input.
    articles = store.all_articles(limit=size * 20)
    filings = [filing_as_article(filing) for filing in store.all_filings(limit=size * 20)]
    documents = articles + filings
    if not documents:
        print("The archive is empty. Run the news ingest first.", file=sys.stderr)
        return 1

    try:
        written = write_sample(documents, size=size)
    except LabelFileError as error:
        print(str(error), file=sys.stderr)
        return 1

    print(f"Wrote {written} rows to {DEFAULT_LABEL_PATH}")
    print(f"Drawn from {len(articles)} articles and {len(filings)} filings.")
    print("Fill in the true_label column with one of:")
    print("  " + ", ".join(sorted(VALID_LABELS)))
    print("Most rows are NONE. That is expected and correct.")
    return 0


# Commands that talk to the chat and the database, and never to the broker.
# A table rather than an if-chain: at seven entries the chain stopped being
# easier to read than the thing it was avoiding.
CHAT_COMMANDS: dict[str, Callable[[], int]] = {
    "telegram-check": run_telegram_check,
    "capture": run_capture_once,
    "nag": run_nag,
    "review": run_review,
    "score": run_scoring,
    "calibration": run_calibration,
    "health": run_health,
}


def _run_chat_command(command: str) -> int:
    """Telegram-only commands. No Dhan credentials are loaded or needed."""
    register_secret(load_telegram_settings().bot_token)
    return CHAT_COMMANDS[command]()


def _run_brief() -> int:
    """The briefing needs both: a broker for chains and a chat to deliver to."""
    register_secret(load_telegram_settings().bot_token)
    return run_briefing()


def _login(force: bool) -> int:
    settings = load_dhan_settings()
    grant = ensure_valid_token(settings, force=force)
    log.info("Authenticated. Token valid for %.1f hours.", hours_remaining(grant))
    return 0


def _run_backtest() -> int:
    """Run every registered strategy and write a result for the dashboard.

    Reads bars and config, nothing from Dhan, so it is dispatched before the
    broker settings load. Costs nothing and spends nothing.
    """
    bars = PostgresBarStore(create_session_factory(create_db_engine(load_infra_settings())))
    request = load_universe_request()
    runner = BacktestRunner(bars)

    results = runner.run(sorted(request.stock_symbols), list(request.index_symbols))
    path = runner.write(results)

    examples = _example_count()
    for result in results:
        _print_result(result, examples)

    print(f"\nwritten to {path} (and latest.json)")
    print("view it with: uv run trading-desk dashboard")
    print(f"more worked examples with: uv run trading-desk backtest --explain {examples + 3}")
    return 0


def _example_count() -> int:
    """How many trades to show in full. Default 2 - enough to see the shape."""
    for index, argument in enumerate(sys.argv):
        if argument == "--explain" and index + 1 < len(sys.argv):
            return int(sys.argv[index + 1])
    return 2


def _print_result(result: StrategyResult, examples: int) -> None:
    """One strategy's summary, the criteria it requires, and worked examples.

    The criteria are printed every run. Without them the report says a strategy
    lost money without ever saying what it was doing, which is unreviewable.
    """
    rate = result.win_rate
    if rate is None:
        print(f"\n{result.strategy}: no trades")
        return

    # The verdict is expectancy, never the win rate. A strategy can clear its
    # theoretical breakeven and still lose money, and one here does.
    verdict = "PROFITABLE" if result.profitable else "LOSES MONEY"
    realised = result.realised_breakeven
    needed = f"{realised:.1%}" if realised is not None else "n/a"

    print(f"\n{'=' * 78}")
    print(f"{result.strategy}  -  {verdict}")
    print(f"{'=' * 78}")
    print(f"  {result.description}")
    print(f"  exits: {result.exits}")
    print(
        f"  {len(result.trades)} trades, win rate {rate:.1%} "
        f"(needs {needed} at its realised win/loss sizes), "
        f"expectancy {result.expectancy or 0:+.2%}"
    )

    print("\n  WHY A TRADE IS TAKEN - every trade below satisfied all of these:")
    for reason in result.trades[0].reasons:
        print(f"    - {reason.name}: needs {reason.threshold}")

    shown = min(examples, len(result.trades))
    print(f"\n  WORKED EXAMPLES ({shown} of {len(result.trades)}):")
    for trade in result.trades[:examples]:
        _print_trade(trade)


def _print_trade(trade: SimulatedTrade) -> None:
    outcome = "WON" if trade.won else "LOST"
    print(
        f"\n    {trade.symbol} {trade.direction.value} "
        f"{trade.entry_date} Rs {trade.entry_price.rupees():,.2f} -> "
        f"{trade.exit_date} Rs {trade.exit_price.rupees():,.2f} "
        f"({trade.sessions_held}d, {trade.exit_reason.value}) "
        f"{trade.net_return:+.2%} {outcome}"
    )
    print("      taken because:")
    for reason in trade.reasons:
        print(f"        - {reason.name}: measured {reason.measured}, needed {reason.threshold}")
    print(f"      market that day: {trade.market_direction.value}", end="")
    print(" (TRADED AGAINST IT)" if trade.against_market else " (traded with it)")
    print(f"      news check: {trade.against_news}")
    if not trade.won:
        print(f"      failed as: {trade.failure_mode.value}")
        print(f"        {trade.failure_mode.explain()}")


def _serve_dashboard() -> int:
    """Serve the backtest dashboard on localhost.

    A plain static server rather than FastAPI: the page is one file and the
    data is one JSON file beside it, so there is no route to get wrong.
    """
    port = DASHBOARD_PORT
    for index, argument in enumerate(sys.argv):
        if argument == "--port" and index + 1 < len(sys.argv):
            port = int(sys.argv[index + 1])

    results = DEFAULT_RESULTS_DIR / "latest.json"
    if not results.exists():
        print(f"No results at {results}. Run: uv run trading-desk backtest", file=sys.stderr)
        return 1

    serve_dashboard(port)
    return 0


def main() -> int:
    command = sys.argv[1] if len(sys.argv) > 1 else "run"

    # Before the Dhan settings load: these need a database or a chat, not a
    # broker. A command that asks for trading credentials it does not use is a
    # command that cannot be run on a laptop without them.
    if command == "label-news":
        configure_logging(load_app_settings().log_level)
        return _write_label_sheet()
    if command == "backtest":
        configure_logging(load_app_settings().log_level)
        return _run_backtest()
    if command == "dashboard":
        configure_logging(load_app_settings().log_level)
        return _serve_dashboard()
    if command in CHAT_COMMANDS:
        configure_logging(load_app_settings().log_level)
        return _run_chat_command(command)

    settings = load_dhan_settings()
    register_secret(settings.api_secret)
    configure_logging(load_app_settings().log_level)

    handlers = {
        "login": lambda: _login(force="--force" in sys.argv),
        "snapshot": _run_capture,
        "survey": _run_survey,
        "run": _start_scheduler,
        "brief": _run_brief,
    }
    handler = handlers.get(command)
    if handler is not None:
        return handler()

    print(f"Unknown command: {command}", file=sys.stderr)
    print(
        "Usage: trading-desk "
        "[run|snapshot|login|survey|label-news|brief|capture|nag|review|score|"
        "calibration|health|telegram-check|backtest|dashboard]",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
