"""The commands that produce and answer a briefing.

Kept out of `cli.py` because that module is already the length of two, and
because these four share a lot of wiring that the capture commands do not need
from the broker.

    trading-desk telegram-check   # prove delivery works, before 08:15 does
    trading-desk brief            # run a cycle and send it
    trading-desk capture          # record whatever is waiting in the chat
    trading-desk nag              # chase anything unanswered after 10:00

**The cursor lives in a file.** Telegram forgets an update once it has been
acknowledged by asking for a later one, so the only state that must survive a
restart is a single integer - and it is only written after the decisions it
covers have been recorded. A crash re-reads the same tap and records the same
decision twice, which is recoverable; advancing first would lose it, which is
not.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from trading_desk.adapters.broker.unlinked import NOT_LINKED, UnlinkedBroker
from trading_desk.adapters.clock import SystemClock
from trading_desk.adapters.dhan.chain import DhanOptionChainAdapter
from trading_desk.adapters.dhan.client import DhanClient
from trading_desk.adapters.dhan.instruments import DhanInstrumentMaster
from trading_desk.adapters.dhan.login import ensure_valid_token
from trading_desk.adapters.dhan.token_store import StoredTokenProvider
from trading_desk.adapters.llm.anthropic_client import AnthropicLlm
from trading_desk.adapters.store.bar_repository import PostgresBarStore
from trading_desk.adapters.store.cycle_store import PostgresCycleStore
from trading_desk.adapters.store.journal import PostgresJournal
from trading_desk.adapters.store.llm_ledger import PostgresLlmLedger
from trading_desk.adapters.store.news_repository import PostgresNewsStore
from trading_desk.adapters.telegram.chat import TelegramChat
from trading_desk.adapters.telegram.client import TelegramClient
from trading_desk.adapters.telegram.notifier import TelegramNotifier
from trading_desk.adapters.tracing.jsonl import JsonlTracer
from trading_desk.agents.fundamental.agent import FundamentalAgent
from trading_desk.agents.news.catalyst_classifier import CatalystClassifier
from trading_desk.agents.news.impact_analyst import ImpactAnalyst
from trading_desk.agents.news.pipeline import NewsAgent
from trading_desk.agents.options.agent import OptionsAgent
from trading_desk.agents.portfolio.agent import PortfolioAgent
from trading_desk.agents.strategist.graph import StrategistGraph
from trading_desk.agents.strategist.synthesis import SynthesisWriter
from trading_desk.agents.technical.agent import TechnicalAgent
from trading_desk.application.briefing import BriefingBuilder, deliver
from trading_desk.application.calendars import load_trading_calendar
from trading_desk.application.decision_capture import DecisionCapture
from trading_desk.application.llm_governor import CostGovernedLlm
from trading_desk.application.news_ingest import load_aliases_from_file
from trading_desk.application.research_cycle import ResearchCycle
from trading_desk.application.universe import load_universe_request, resolve_universe
from trading_desk.domain.setups.thresholds import load_setup_thresholds
from trading_desk.domain.values import Money
from trading_desk.infra.config import (
    load_dhan_settings,
    load_infra_settings,
    load_llm_settings,
    load_telegram_settings,
)
from trading_desk.infra.db import create_db_engine, create_session_factory
from trading_desk.infra.llm_pricing import load_llm_pricing
from trading_desk.infra.risk_config import load_risk_config
from trading_desk.ports.notifier import AlertLevel

log = logging.getLogger("trading_desk.briefing")

CURSOR_PATH = Path("data/telegram_cursor.txt")

# The wing offset for a credit spread is not in config/setups.yaml and decides
# the max loss, so it is named here with its own note rather than buried.
# Pending Ajay's sign-off (STATE "Blocked on you").
CREDIT_WING_OFFSET = Money.from_rupees(Decimal("200"))


def _telegram() -> TelegramClient:
    settings = load_telegram_settings()
    return TelegramClient(bot_token=settings.bot_token.get_secret_value(), chat_id=settings.chat_id)


def run_telegram_check() -> int:
    """Send one message, so delivery is proven before a briefing depends on it."""
    clock = SystemClock()
    client = _telegram()
    try:
        receipt = TelegramNotifier(client, clock).send_alert(
            AlertLevel.INFO,
            "trading-desk is wired up. This is the only test message it will send.",
        )
    finally:
        client.close()
    log.info("Delivered. Telegram message id %s at %s", receipt.message_id, receipt.delivered_at)
    return 0


def _capture_service(client: TelegramClient) -> DecisionCapture:
    clock = SystemClock()
    factory = create_session_factory(create_db_engine(load_infra_settings()))
    return DecisionCapture(
        chat=TelegramChat(client),
        journal=PostgresJournal(factory, clock),
        notifier=TelegramNotifier(client, clock),
        clock=clock,
    )


def run_capture_once() -> int:
    """Record every decision waiting in the chat, then advance the cursor."""
    client = _telegram()
    try:
        report = _capture_service(client).drain(_read_cursor())
        _write_cursor(report.last_update_id)
    finally:
        client.close()

    for line in report.as_lines():
        log.info("%s", line)
    return 0


def run_nag() -> int:
    """Chase anything from today that still has no decision."""
    client = _telegram()
    try:
        sent = _capture_service(client).nag_if_due()
    finally:
        client.close()

    log.info("%s", "nagged" if sent else "nothing to chase (or it is before 10:00 IST)")
    return 0


def run_briefing() -> int:
    """One research cycle, delivered. The 08:15 job."""
    clock = SystemClock()
    factory = create_session_factory(create_db_engine(load_infra_settings()))
    pricing = load_llm_pricing()
    cycle_store = PostgresCycleStore(factory)

    governor = CostGovernedLlm(
        client=AnthropicLlm(load_llm_settings().anthropic_api_key.get_secret_value(), pricing),
        ledger=PostgresLlmLedger(factory),
        pricing=pricing,
        clock=clock,
        tracer=JsonlTracer(),
        cycles=cycle_store,
    )

    dhan = load_dhan_settings()
    ensure_valid_token(dhan)
    dhan_client = DhanClient(client_id=dhan.client_id, token_provider=StoredTokenProvider())

    try:
        return _run_cycle_and_send(clock, factory, governor, cycle_store, dhan_client)
    finally:
        dhan_client.close()


def _run_cycle_and_send(
    clock: SystemClock,
    factory: object,
    governor: CostGovernedLlm,
    cycle_store: PostgresCycleStore,
    dhan_client: DhanClient,
) -> int:
    from sqlalchemy.orm import Session, sessionmaker  # noqa: PLC0415 - typing only

    sessions: sessionmaker[Session] = factory  # type: ignore[assignment]
    today = clock.utc_now().date()
    calendar = load_trading_calendar()
    thresholds = load_setup_thresholds()
    chains = DhanOptionChainAdapter(dhan_client, clock)
    news_store = PostgresNewsStore(sessions)
    journal = PostgresJournal(sessions, clock)

    request_config = load_universe_request()
    catalogue = DhanInstrumentMaster().option_underlyings(today)
    universe = resolve_universe(request_config, catalogue)

    graph = StrategistGraph(
        news=NewsAgent(
            classifier=CatalystClassifier(governor),
            analyst=ImpactAnalyst(governor),
            aliases=load_aliases_from_file(),
        ),
        fundamental=FundamentalAgent(news_store),
        technical=TechnicalAgent(PostgresBarStore(sessions)),
        options=OptionsAgent(chains, thresholds, CREDIT_WING_OFFSET),
        portfolio=PortfolioAgent(
            UnlinkedBroker(), journal, chains, frozenset(request_config.index_symbols)
        ),
        synthesis=SynthesisWriter(governor),
        store=cycle_store,
        clock=clock,
        thresholds=thresholds,
    )
    cycle = ResearchCycle(
        graph=graph,
        governor=governor,
        journal=journal,
        news_store=news_store,
        clock=clock,
    )

    request = cycle.build_request(
        universe=tuple(universe.all_instruments()),
        # No IV rank is supplied here: `application.iv_rank` needs the chain
        # archive, and wiring it is step 3.7's job when the cycle is scheduled.
        # Until then every idea dies at the volatility gate, visibly.
        iv_ranks={},
        holidays=calendar.holidays,
        cycle_id=uuid4(),
    )
    output = cycle.run(
        request.__class__(
            cycle_id=request.cycle_id,
            as_of=request.as_of,
            universe=request.universe,
            articles=request.articles,
            iv_ranks=request.iv_ranks,
            holidays=request.holidays,
            extra_missing_inputs=(NOT_LINKED,),
        )
    )

    briefing = BriefingBuilder(load_risk_config(), journal).build(output)
    telegram = _telegram()
    try:
        receipts = deliver(briefing, TelegramNotifier(telegram, clock))
    finally:
        telegram.close()

    log.info(
        "Briefing delivered in %d message(s); %d idea(s); cost %s",
        len(receipts),
        len(output.result.proposals),
        output.costs.total,
    )
    return 0


def _read_cursor() -> int:
    if not CURSOR_PATH.is_file():
        return 0
    return int(CURSOR_PATH.read_text().strip() or 0)


def _write_cursor(update_id: int) -> None:
    CURSOR_PATH.parent.mkdir(parents=True, exist_ok=True)
    CURSOR_PATH.write_text(str(update_id))
