"""One fully-wired strategist graph over fakes, and inputs that pass every gate.

Building a cycle that actually produces an idea takes work, and that is the
useful part: **all nine Setup A gates have to pass at once**, so the fixtures
below are a worked example of what a tradable day looks like. Each number is
chosen against a specific gate and the comments say which.

Everything here runs offline and costs nothing: the model is `FakeLlm`, which
returns exactly what a test queued.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from tests.fakes import (
    FakeBarStore,
    FakeBrokerRead,
    FakeCycleStore,
    FakeJournal,
    FakeLlm,
    FakeLlmLedger,
    FakeNewsStore,
    FakeOptionChain,
    FrozenClock,
)
from trading_desk.agents.fundamental.agent import FundamentalAgent
from trading_desk.agents.news.catalyst_classifier import CatalystClassifier, CatalystResponse
from trading_desk.agents.news.impact_analyst import ImpactAnalyst, ImpactResponse
from trading_desk.agents.news.pipeline import NewsAgent
from trading_desk.agents.options.agent import OptionsAgent
from trading_desk.agents.portfolio.agent import PortfolioAgent
from trading_desk.agents.strategist.graph import CycleRequest, StrategistGraph
from trading_desk.agents.strategist.synthesis import SynthesisResponse, SynthesisWriter
from trading_desk.agents.technical.agent import TechnicalAgent
from trading_desk.application.llm_governor import CostGovernedLlm
from trading_desk.application.research_cycle import ResearchCycle
from trading_desk.domain.catalysts import CatalystType
from trading_desk.domain.entities import AliasTable
from trading_desk.domain.enums import Direction, Exchange, OptionRight, Segment, SourceTier
from trading_desk.domain.instruments import InstrumentRef
from trading_desk.domain.llm_cost import LlmPricing, ModelPrice, ModelTier
from trading_desk.domain.market import Bar, OptionChainSnapshot, OptionQuote, OptionStrike
from trading_desk.domain.news import NewsArticle
from trading_desk.domain.setups.thresholds import load_setup_thresholds
from trading_desk.domain.values import Money, Price
from trading_desk.domain.vol import IvRank, IvSource

CYCLE_ID = UUID("11111111-2222-3333-4444-555555555555")

# A Monday morning run. The catalyst landed the previous session, so the
# recency gate sees one session (limit is two).
AS_OF = datetime(2026, 10, 19, 4, 0, tzinfo=UTC)  # 09:30 IST
PUBLISHED = datetime(2026, 10, 16, 10, 0, tzinfo=UTC)
EXPIRY = date(2026, 11, 5)  # 17 days out; the window is 10-25

RELIANCE = InstrumentRef(
    symbol="RELIANCE",
    exchange=Exchange.NSE,
    segment=Segment.EQUITY,
    security_id="2885",
    lot_size=250,
)

ALIASES = AliasTable(aliases={"RELIANCE": ("reliance industries", "reliance")})

# Short enough to appear verbatim in every company's story, so one queued
# classifier response is grounded against all of them.
ORDER_QUOTE = "secured an order worth Rs"

# One story per company, and they have to read differently: the news agent
# dedups on content, and three paraphrases of the same sentence group into one
# story however different the company name is. The first version of this
# harness got that wrong and two symbols silently became one.
STORY_DETAIL = {
    "RELIANCE": ("4,500", "NTPC", "equipment supply over three years"),
    "INFY": ("2,100", "Lloyds Banking Group", "a seven-year digital transformation"),
    "TCS": ("8,300", "the Ministry of Railways", "signalling systems modernisation"),
}


def pricing() -> LlmPricing:
    return LlmPricing(
        tiers={
            ModelTier.TRIAGE: ModelPrice(
                model="claude-haiku-5-5",
                input_usd_per_million=Decimal("0.10"),
                output_usd_per_million=Decimal("0.50"),
            ),
            ModelTier.SYNTHESIS: ModelPrice(
                model="claude-opus-5",
                input_usd_per_million=Decimal("5.00"),
                output_usd_per_million=Decimal("25.00"),
            ),
        },
        usd_to_inr=Decimal("89"),
        monthly_cap=Money.from_rupees(Decimal("2000")),
        verified=False,
        source="test",
    )


# --------------------------------------------------------------------- news


def instrument_for(symbol: str) -> InstrumentRef:
    return InstrumentRef(
        symbol=symbol,
        exchange=Exchange.NSE,
        segment=Segment.EQUITY,
        security_id=f"id-{symbol}",
        lot_size=250,
    )


def _article(article_id: str, publisher: str, symbol: str = "RELIANCE") -> NewsArticle:
    """Tier 1, attributed, and citing a filing - so the credibility score
    clears Setup A's 0.70 floor. A 'sources say' version of the same story
    would be capped at 0.65 and die at that gate."""
    amount, counterparty, work = STORY_DETAIL[symbol]
    return NewsArticle(
        article_id=article_id,
        headline=f"{symbol} wins Rs {amount} crore order from {counterparty}",
        body=(
            f"{symbol} said in an exchange filing that it has "
            f"{ORDER_QUOTE} {amount} crore from {counterparty} for {work}. "
            f"The {counterparty} contract will be executed by {symbol}."
        ),
        url=f"https://example.invalid/{article_id}",
        publisher=publisher,
        source_tier=SourceTier.TIER_1,
        published_at=PUBLISHED,
        ingested_at=PUBLISHED,
        instrument_symbols=(symbol,),
    )


def two_outlets() -> list[NewsArticle]:
    """The same announcement from two publishers.

    Two, not one, because `Thesis` requires two independent sources (FR-4.6).
    A single-source idea is rejected, and that is tested separately.
    """
    return two_outlets_for("RELIANCE")


def two_outlets_for(symbol: str) -> list[NewsArticle]:
    """The same announcement about one company, from two publishers."""
    return [
        _article(f"{symbol}-1", "Wire A", symbol),
        _article(f"{symbol}-2", "Wire B", symbol),
    ]


def catalyst_response() -> CatalystResponse:
    return CatalystResponse(
        catalyst_type=CatalystType.LARGE_ORDER_WIN,
        confidence=0.9,
        evidence_quote=ORDER_QUOTE,
        reasoning="an order win stated in an exchange filing",
    )


def impact_response() -> ImpactResponse:
    return ImpactResponse(
        direction=Direction.BULLISH,
        horizon_days=10,
        already_priced=False,
        supporting_quote=ORDER_QUOTE,
    )


def synthesis_response() -> SynthesisResponse:
    """Deliberately free of digits, so the grounding check has nothing to catch."""
    return SynthesisResponse(
        claim="A large order win gives Reliance a visible revenue addition the market "
        "has only begun to price.",
        invalidation="The thesis is wrong if the stock closes back inside its prior trading range.",
        rationale="The order was stated in an exchange filing and carried by two outlets.",
    )


# --------------------------------------------------------------------- bars


def bars_that_confirm(instrument: InstrumentRef = RELIANCE) -> list[Bar]:
    """Eighty quiet sessions, then a break on volume.

    - ATR(14) lands at about ten rupees, so `not_already_priced` has a
      denominator (it reports NO_DATA on a flat series, which blocks).
    - The last close is above every prior high, so the range break is true.
    - The last volume is 3x the prior mean, clearing the 1.5x floor.
    - The move since the catalyst is under one ATR, so the edge is not gone.
    """
    start = datetime(2026, 6, 1, tzinfo=UTC)
    quiet = [
        Bar(
            instrument=instrument,
            start=start + timedelta(days=index),
            open=Price.from_rupees(Decimal("1400")),
            high=Price.from_rupees(Decimal("1405")),
            low=Price.from_rupees(Decimal("1395")),
            close=Price.from_rupees(Decimal("1400")),
            volume=100_000,
        )
        for index in range(79)
    ]
    breakout = Bar(
        instrument=instrument,
        start=start + timedelta(days=79),
        open=Price.from_rupees(Decimal("1400")),
        high=Price.from_rupees(Decimal("1409")),
        low=Price.from_rupees(Decimal("1399")),
        close=Price.from_rupees(Decimal("1408")),
        volume=300_000,
    )
    return [*quiet, breakout]


# -------------------------------------------------------------------- chain


def _quote(right: OptionRight, mid: str, delta: float) -> OptionQuote:
    """Spreads are tight on purpose: Setup A's liquidity gate allows 3% of mid
    per leg, and the stock chains in other fixtures are wider than that."""
    centre = Decimal(mid)
    half = centre * Decimal("0.01")
    return OptionQuote(
        right=right,
        last=Price.from_rupees(centre),
        bid=Price.from_rupees(centre - half),
        ask=Price.from_rupees(centre + half),
        volume=2000,
        open_interest=5000,
        implied_volatility=18.0,
        delta=delta,
    )


def tradable_chain(instrument: InstrumentRef = RELIANCE) -> OptionChainSnapshot:
    rows = [
        ("1360", "62", 0.78, "7", -0.22),
        ("1380", "50", 0.65, "12", -0.35),
        ("1400", "40", 0.52, "20", -0.48),  # long leg: 0.50-0.55
        ("1420", "28", 0.40, "32", -0.60),
        ("1440", "18", 0.28, "46", -0.72),  # short leg: 0.25-0.30
        ("1460", "11", 0.18, "62", -0.82),
    ]
    strikes = tuple(
        OptionStrike(
            strike=Price.from_rupees(Decimal(strike)),
            call=_quote(OptionRight.CALL, call_mid, call_delta),
            put=_quote(OptionRight.PUT, put_mid, put_delta),
        )
        for strike, call_mid, call_delta, put_mid, put_delta in rows
    )
    return OptionChainSnapshot(
        underlying=instrument,
        expiry_date=EXPIRY.isoformat(),
        strikes=strikes,
        spot=Price.from_rupees(Decimal("1408")),
        exchange_time=AS_OF,
        received_at=AS_OF,
    )


# ------------------------------------------------------------------- wiring


class Harness:
    """Every fake the graph needs, kept so a test can look inside afterwards."""

    def __init__(
        self, store: FakeCycleStore | None = None, ledger: FakeLlmLedger | None = None
    ) -> None:
        # A resumed cycle is a *new process* reading the *same* store, so the
        # two are injectable. Everything else starts empty, which is the point.
        self.client = FakeLlm()
        self.ledger = ledger or FakeLlmLedger()
        self.store = store or FakeCycleStore()
        self.journal = FakeJournal()
        self.news_store = FakeNewsStore()
        self.bars = FakeBarStore()
        self.chains = FakeOptionChain()
        self.broker = FakeBrokerRead()
        self.broker.funds = Money.from_rupees(Decimal("500000"))
        self.clock = FrozenClock(AS_OF)

        self.universe: list[InstrumentRef] = [RELIANCE]
        self.bars.write_daily_bars(bars_that_confirm(), "test")
        self.chains.add_chain(tradable_chain(), EXPIRY)

        self.governor = CostGovernedLlm(
            client=self.client,
            ledger=self.ledger,
            pricing=pricing(),
            clock=self.clock,
            cycles=self.store,
        )
        thresholds = load_setup_thresholds()
        self.graph = StrategistGraph(
            news=NewsAgent(
                classifier=CatalystClassifier(self.governor),
                analyst=ImpactAnalyst(self.governor),
                aliases=ALIASES,
            ),
            fundamental=FundamentalAgent(self.news_store),
            technical=TechnicalAgent(self.bars),
            options=OptionsAgent(self.chains, thresholds, Money.from_rupees(Decimal("40"))),
            portfolio=PortfolioAgent(
                self.broker, self.journal, self.chains, frozenset({"NIFTY", "BANKNIFTY"})
            ),
            synthesis=SynthesisWriter(self.governor),
            store=self.store,
            clock=self.clock,
            thresholds=thresholds,
        )
        self.cycle = ResearchCycle(
            graph=self.graph,
            governor=self.governor,
            journal=self.journal,
            news_store=self.news_store,
            clock=self.clock,
        )

    def queue_a_full_cycle(self, synthesis: SynthesisResponse | None = None) -> None:
        """Classify once, assess impact once, then write once."""
        self.client.queue(catalyst_response(), impact_response(), synthesis or synthesis_response())

    def add_symbol(self, symbol: str) -> InstrumentRef:
        """A second tradable name, identical in every measurable way.

        Identical on purpose: when two candidates score the same, the only
        thing deciding their order is the tie-break, which is exactly what the
        determinism check needs to exercise.
        """
        instrument = instrument_for(symbol)
        self.universe.append(instrument)
        self.bars.write_daily_bars(bars_that_confirm(instrument), "test")
        self.chains.add_chain(tradable_chain(instrument), EXPIRY)
        return instrument

    def iv_ranks(self) -> dict[str, IvRank]:
        rank = IvRank(value=40.0, source=IvSource.PROXY, sample_count=130, window=252)
        return {item.symbol: rank for item in self.universe}

    def request(self, articles: list[NewsArticle] | None = None) -> CycleRequest:
        return CycleRequest(
            cycle_id=CYCLE_ID,
            as_of=AS_OF,
            universe=tuple(self.universe),
            articles=two_outlets() if articles is None else articles,
            iv_ranks=self.iv_ranks(),
            holidays=frozenset(),
        )
