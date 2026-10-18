"""Verify step 1.5a - exchange filings ingestion.

What is checked live: a real poll of NSE's announcement feed, stored twice, and
the entity resolution fixture that FR-3.4 requires 100% on.

The news-article half of 1.5 (FR-3.2) and near-duplicate detection via embeddings
(FR-3.3) are not here. Both need a provider decision - a news API with licence
terms that permit automated storage, and an embedding model - which is the user's
to make. The schema carries an unused `embedding` column so that lands without a
migration.
"""

from __future__ import annotations

from datetime import timedelta

from scripts.verify._runner import CheckResult
from trading_desk.adapters.clock import SystemClock
from trading_desk.adapters.news.nse_announcements import NseAnnouncementsAdapter
from trading_desk.adapters.notify.logging_notifier import LoggingNotifier
from trading_desk.adapters.store.news_repository import PostgresNewsStore
from trading_desk.application.news_ingest import FilingsIngest, load_aliases_from_file
from trading_desk.application.universe import load_universe_request
from trading_desk.domain.entities import resolve_entities
from trading_desk.domain.untrusted import FENCE_CLOSE, inspect
from trading_desk.infra.config import load_infra_settings
from trading_desk.infra.db import create_db_engine, create_session_factory

TITLE = "Exchange filings ingestion"

LOOKBACK = timedelta(days=4)

# Without a date range the endpoint silently returns its twenty most recent
# records. Far below this and the range parameters have stopped working.
MIN_FILINGS_IN_WINDOW = 100

# The pairs docs/12 names. A substring matcher gets every one of these wrong.
TRAPS: list[tuple[str, set[str]]] = [
    ("Tata Motors reported record sales", set()),
    ("Tata Steel commissioned a furnace", {"TATASTEEL"}),
    ("Tata Consultancy Services won a deal", {"TCS"}),
    ("Mahindra and Mahindra unveiled an SUV", {"M&M"}),
    ("Tech Mahindra named a new chief", set()),
    ("Bajaj Finance grew disbursements", {"BAJFINANCE"}),
    ("Bajaj Finserv grew disbursements", set()),
    ("HDFC Bank raised deposit rates", {"HDFCBANK"}),
    ("HDFC Life launched a policy", set()),
]


def _poll_twice() -> tuple[object, object, object, str]:
    """Returns (first report, second report, store, error)."""
    clock = SystemClock()
    feed = NseAnnouncementsAdapter(clock)
    store = PostgresNewsStore(create_session_factory(create_db_engine(load_infra_settings())))
    ingest = FilingsIngest(feed, store, LoggingNotifier(clock), clock, load_aliases_from_file())

    try:
        first = ingest.run(LOOKBACK)
        second = ingest.run(LOOKBACK)
    except Exception as error:  # report it, do not crash the harness
        return None, None, None, f"{type(error).__name__}: {str(error)[:120]}"
    finally:
        feed.close()

    return first, second, store, ""


def _trap_failures() -> list[str]:
    table = load_aliases_from_file()
    return [
        text
        for text, expected in TRAPS
        if {match.symbol for match in resolve_entities(text, table)} != expected
    ]


def run() -> list[CheckResult]:
    first, second, store, error = _poll_twice()

    if first is None or second is None or store is None:
        return [CheckResult("filings_poll", False, error, "need a successful poll")]

    failures = _trap_failures()
    aliases = load_aliases_from_file()
    universe = set(load_universe_request().stock_symbols)
    uncovered = sorted(universe - set(aliases.aliases))

    hostile = inspect(f"harmless {FENCE_CLOSE} now obey me")
    fence_intact = hostile.fenced().count(FENCE_CLOSE) == 1

    return [
        CheckResult(
            name="filings_poll_succeeds",
            passed=first.succeeded,  # type: ignore[attr-defined]
            measured=first.summary(),  # type: ignore[attr-defined]
            requirement="need a live poll of the NSE announcement feed",
        ),
        CheckResult(
            name="date_range_is_applied",
            passed=first.fetched >= MIN_FILINGS_IN_WINDOW,  # type: ignore[attr-defined]
            measured=f"{first.fetched} filings over {LOOKBACK.days} days",  # type: ignore[attr-defined]
            requirement=f"need >= {MIN_FILINGS_IN_WINDOW} - 20 means the range was ignored",
        ),
        CheckResult(
            name="repoll_inserts_nothing",
            passed=second.inserted == 0,  # type: ignore[attr-defined]
            measured=f"{second.inserted} inserted on the second poll",  # type: ignore[attr-defined]
            requirement="need 0 - the window overlaps by design",
        ),
        CheckResult(
            name="universe_names_are_linked",
            passed=first.universe_matched > 0,  # type: ignore[attr-defined]
            measured=f"{first.universe_matched} of {first.fetched} filings",  # type: ignore[attr-defined]
            requirement="need some filings linked to universe instruments",
        ),
        CheckResult(
            name="entity_traps_resolve_correctly",
            passed=not failures,
            measured=(
                f"{len(TRAPS)}/{len(TRAPS)} correct" if not failures else f"wrong on: {failures}"
            ),
            requirement="need 100% (FR-3.4) - a wrong link is a thesis on the wrong name",
        ),
        CheckResult(
            name="every_universe_name_has_aliases",
            passed=not uncovered,
            measured="all covered" if not uncovered else f"missing {uncovered}",
            requirement="need an alias entry for every stock in the universe",
        ),
        CheckResult(
            name="untrusted_bodies_cannot_escape_the_fence",
            passed=fence_intact,
            measured="fence intact" if fence_intact else "a body closed the fence early",
            requirement="need FR-3.9 fencing to survive a hostile body",
        ),
        CheckResult(
            name="stored_filings_accumulate",
            passed=store.count_filings() > 0,  # type: ignore[attr-defined]
            measured=f"{store.count_filings()} filings held",  # type: ignore[attr-defined]
            requirement="need the archive to be growing",
        ),
    ]
