"""Verify step 1.5b - news articles from regulatory sources.

**One feed is configured: SEBI.** Sixteen were surveyed on 2026-10-18 and every
commercial publisher whose terms could be read prohibits this use - Economic
Times and Times of India ("any other automated or technology-enabled
summarization or aggregation"), Mint ("robot", "spider" or other automatic
device), CNBC TV18 ("creating large language models and/or training of A.I.
tools"). RBI was excluded despite being a regulator and despite a live, fresh
feed, because its Terms & Conditions of Use state "caching and links to, and
the framing of this Web Site or any of the contents are prohibited" except by
prior written permission. See `config/news_sources.yaml`, clauses quoted.

So the checks here are in two halves:

- **Posture**: no forbidding publisher may be configured, every exclusion must
  carry its reason, and every configured feed must carry its compliance
  evidence. These guard against a feed being re-added casually.
- **Function**: the SEBI feed must actually ingest. Two faults here are silent
  and both occurred - a TLS chain the system store cannot verify, and a
  `pubDate` the standard parser raises on. Each returned an empty list that was
  indistinguishable from a quiet news day, so the adapter now reports failures
  and this step proves it does.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import text

from scripts.verify._runner import CheckResult
from trading_desk.adapters.clock import SystemClock
from trading_desk.adapters.news.rss import RssNewsAdapter, load_source_policy
from trading_desk.adapters.notify.logging_notifier import LoggingNotifier
from trading_desk.adapters.store.news_repository import PostgresNewsStore
from trading_desk.application.news_ingest import ArticleIngest, load_aliases_from_file
from trading_desk.domain.similarity import SimilarDocument, group_near_duplicates, similarity
from trading_desk.infra.config import load_infra_settings
from trading_desk.infra.db import create_db_engine, create_session_factory

TITLE = "News articles: regulatory feeds"

SOURCES_PATH = Path("config/news_sources.yaml")

# Hosts whose terms forbid this use. Configuring one is a regression, not a
# judgement call, so it fails rather than warns. RBI is here despite being a
# regulator: its terms prohibit caching absent written permission.
FORBIDDEN_HOSTS = (
    "economictimes.indiatimes.com",
    "timesofindia.indiatimes.com",
    "livemint.com",
    "hindustantimes.com",
    "business-standard.com",
    "cnbctv18.com",
    "thehindubusinessline.com",
    "rbi.org.in",
)

CROSS_COMPANY = (
    "TCS wins $1 billion deal with European retailer",
    "Infosys wins $1 billion deal with European retailer",
)
HIGH_SIMILARITY = 0.7
EXPECTED_GROUPS = 2

LOOKBACK = timedelta(days=4)


def _raw_config() -> dict[str, Any]:
    return yaml.safe_load(SOURCES_PATH.read_text()) or {}


def _no_forbidden_feed_configured() -> tuple[bool, str]:
    urls = [str(entry.get("url", "")) for entry in (_raw_config().get("feeds") or [])]
    offending = [url for url in urls if any(host in url for host in FORBIDDEN_HOSTS)]
    if offending:
        return False, f"configured despite forbidding terms: {offending}"
    return True, f"{len(urls)} feed(s) configured, none from a forbidding publisher"


def _exclusions_are_documented() -> tuple[bool, str]:
    excluded = _raw_config().get("excluded") or []
    undocumented = [
        str(entry.get("name", "?"))
        for entry in excluded
        if not str(entry.get("reason", "")).strip()
    ]
    return not undocumented, (
        f"{len(excluded)} excluded, all with a stated reason"
        if not undocumented
        else f"no reason given for: {undocumented}"
    )


def _every_feed_cites_its_terms() -> tuple[bool, str]:
    """A configured feed must show the evidence it is allowed, not just a URL."""
    feeds = _raw_config().get("feeds") or []
    if not feeds:
        return False, "no feed configured"

    missing = [
        str(entry.get("name", "?"))
        for entry in feeds
        if not str(entry.get("terms", "")).strip() or not str(entry.get("compliance", "")).strip()
    ]
    return not missing, (
        f"{len(feeds)} feed(s), each citing terms and the clause relied on"
        if not missing
        else f"no terms/compliance evidence for: {missing}"
    )


def _live_ingest() -> tuple[Any, Any]:
    clock = SystemClock()
    policy = load_source_policy()
    feed = RssNewsAdapter(policy, clock)
    store = PostgresNewsStore(create_session_factory(create_db_engine(load_infra_settings())))
    ingest = ArticleIngest(
        news=feed,
        store=store,
        notifier=LoggingNotifier(clock),
        clock=clock,
        aliases=load_aliases_from_file(),
        sources=tuple((item.name, str(item.tier), item.tier_reason) for item in policy.feeds),
    )
    try:
        return ingest.run(LOOKBACK), policy
    finally:
        feed.close()


def _feed_returns_articles(report: Any) -> tuple[bool, str]:
    """The headline check: the configured feed produces usable, dated items.

    This failed twice for reasons that looked identical from outside - a TLS
    root the system store lacks, and a pubDate the RFC 822 parser rejects.
    """
    if not report.succeeded:
        return False, report.summary()
    return report.fetched > 0, report.summary()


def _no_feed_failed(report: Any) -> tuple[bool, str]:
    if report.failed_feeds:
        detail = ", ".join(f"{item.name}: {item.reason}" for item in report.failed_feeds)
        return False, detail
    return True, "every configured feed answered and parsed"


def _stored_articles_come_only_from_permitted_sources() -> tuple[bool, str]:
    """Nothing gathered before the terms were read may remain in the table."""
    permitted = {str(entry["name"]) for entry in (_raw_config().get("feeds") or [])}
    engine = create_db_engine(load_infra_settings())
    with engine.connect() as connection:
        rows = connection.execute(
            text("SELECT publisher, count(*) FROM articles GROUP BY publisher")
        ).all()

    stray = [(name, count) for name, count in rows if name not in permitted]
    total = sum(count for _, count in rows)
    if stray:
        return False, f"articles from non-configured publishers: {stray}"
    return True, f"{total} article(s), all from {sorted(permitted)}"


def _partition_blocks_a_cross_company_merge() -> tuple[bool, str]:
    score = similarity(*CROSS_COMPANY)
    groups = group_near_duplicates(
        [
            SimilarDocument("tcs", CROSS_COMPANY[0], frozenset({"TCS"})),
            SimilarDocument("infy", CROSS_COMPANY[1], frozenset({"INFY"})),
        ]
    )
    return (
        score > HIGH_SIMILARITY and len(groups) == EXPECTED_GROUPS,
        f"similarity {score:.2f}, {len(groups)} groups",
    )


def run() -> list[CheckResult]:
    forbidden_ok, forbidden_detail = _no_forbidden_feed_configured()
    documented_ok, documented_detail = _exclusions_are_documented()
    cited_ok, cited_detail = _every_feed_cites_its_terms()

    try:
        report, _policy = _live_ingest()
    except Exception as error:  # report it, do not crash the harness
        detail = f"{type(error).__name__}: {str(error)[:120]}"
        fetched_ok, fetched_detail = False, detail
        failures_ok, failures_detail = False, detail
    else:
        fetched_ok, fetched_detail = _feed_returns_articles(report)
        failures_ok, failures_detail = _no_feed_failed(report)

    stored_ok, stored_detail = _stored_articles_come_only_from_permitted_sources()
    partition_ok, partition_detail = _partition_blocks_a_cross_company_merge()

    return [
        CheckResult(
            name="no_feed_from_a_forbidding_publisher",
            passed=forbidden_ok,
            measured=forbidden_detail,
            requirement="need zero feeds whose terms prohibit automated aggregation",
        ),
        CheckResult(
            name="every_exclusion_states_its_reason",
            passed=documented_ok,
            measured=documented_detail,
            requirement="need a reason per exclusion, so none is re-added by accident",
        ),
        CheckResult(
            name="every_feed_cites_the_terms_it_relies_on",
            passed=cited_ok,
            measured=cited_detail,
            requirement="need terms URL and the permitting clause recorded per feed",
        ),
        CheckResult(
            name="configured_feed_returns_dated_articles",
            passed=fetched_ok,
            measured=fetched_detail,
            requirement="need > 0 fetched - a TLS or date-format fault returns 0 silently",
        ),
        CheckResult(
            name="no_feed_reported_a_failure",
            passed=failures_ok,
            measured=failures_detail,
            requirement="need zero unreadable feeds; a 403 must not read as a quiet day",
        ),
        CheckResult(
            name="stored_articles_come_only_from_permitted_sources",
            passed=stored_ok,
            measured=stored_detail,
            requirement="need every publisher in the table to be a configured feed",
        ),
        CheckResult(
            name="grouping_still_partitions_by_instrument",
            passed=partition_ok,
            measured=partition_detail,
            requirement="need 2 groups despite high text similarity",
        ),
    ]
