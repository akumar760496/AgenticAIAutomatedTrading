"""Every port has a fake, and the fakes behave like the real thing will.

These tests are about the seams rather than the logic: that the contracts exist,
that the fakes satisfy them, and that the network is genuinely blocked.
"""

from __future__ import annotations

import socket
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import BaseModel

from tests.fakes import (
    FakeBrokerRead,
    FakeJournal,
    FakeLlm,
    FakeMarketData,
    FakeNews,
    FakeNotifier,
    FakeOptionChain,
)
from trading_desk.domain.enums import Direction, Exchange, Segment, SourceTier
from trading_desk.domain.instruments import InstrumentRef
from trading_desk.domain.market import Bar, Quote
from trading_desk.domain.news import NewsArticle
from trading_desk.domain.values import Money, Price
from trading_desk.ports.broker import BrokerReadPort
from trading_desk.ports.journal import JournalPort, RecordedDecision, RecordedIdea
from trading_desk.ports.llm import LlmPort
from trading_desk.ports.market_data import MarketDataPort
from trading_desk.ports.news import NewsPort
from trading_desk.ports.notifier import AlertLevel, NotifierPort
from trading_desk.ports.option_chain import OptionChainPort

NOW = datetime(2026, 10, 16, 4, 0, tzinfo=UTC)


def _reliance() -> InstrumentRef:
    return InstrumentRef(
        symbol="RELIANCE",
        exchange=Exchange.NSE,
        segment=Segment.EQUITY,
        security_id="2885",
    )


class TestFakesSatisfyTheirPorts:
    """Protocols are structural, so this is a real check, not a formality."""

    def test_market_data(self) -> None:
        fake: MarketDataPort = FakeMarketData()
        assert fake.is_fresh(NOW) is True

    def test_option_chain(self) -> None:
        fake: OptionChainPort = FakeOptionChain()
        assert fake.available_expiries(_reliance()) == []

    def test_news(self) -> None:
        """Only NewsPort remains. FilingsPort and its fake were removed on
        2026-10-20 (ISS-041): the one implementation scraped NSE, which their
        Terms of Use prohibit."""
        news: NewsPort = FakeNews()
        assert news.fetch_since(NOW, []) == []

    def test_llm(self) -> None:
        fake = FakeLlm()
        port: LlmPort = fake  # the binding is the check; Protocols are structural
        assert port is fake
        assert fake.calls == []

    def test_notifier(self) -> None:
        fake: NotifierPort = FakeNotifier()
        assert fake.send_briefing("hello").message_id.startswith("fake-")

    def test_journal(self) -> None:
        fake: JournalPort = FakeJournal()
        assert fake.resolved_ideas(10) == []

    def test_broker_read(self) -> None:
        fake: BrokerReadPort = FakeBrokerRead()
        assert fake.fetch_positions() == []


class TestBrokerPortCannotTrade:
    def test_there_is_no_order_method(self) -> None:
        """v1's safety rests on this absence, so it is asserted rather than assumed."""
        forbidden = ["place_order", "modify_order", "cancel_order", "submit", "square_off"]
        on_port = dir(BrokerReadPort)
        on_fake = dir(FakeBrokerRead)

        for name in forbidden:
            assert name not in on_port, f"BrokerReadPort must not expose {name}"
            assert name not in on_fake, f"FakeBrokerRead must not expose {name}"


class TestFakesRefuseToInvent:
    def test_market_data_raises_for_an_unset_quote(self) -> None:
        with pytest.raises(LookupError, match="add one in the test"):
            FakeMarketData().fetch_quote(_reliance())

    def test_llm_raises_on_an_unqueued_call(self) -> None:
        class Answer(BaseModel):
            text: str

        with pytest.raises(AssertionError, match="Queue a response"):
            FakeLlm().complete("sys", "user", Answer, "claude-haiku-5-5")


class TestFakesCanFailOnPurpose:
    """Fail-closed behaviour is only real if the failure path is exercised."""

    def test_chain_fetch_can_be_made_to_fail(self) -> None:
        fake = FakeOptionChain()
        fake.fail_next_fetch = True

        with pytest.raises(ConnectionError):
            fake.fetch_chain(_reliance(), NOW.date())

    def test_delivery_can_be_made_to_fail(self) -> None:
        fake = FakeNotifier()
        fake.fail_next_send = True

        with pytest.raises(ConnectionError):
            fake.send_briefing("briefing")

    def test_stale_feed_is_representable(self) -> None:
        fake = FakeMarketData()
        fake.fresh = False

        assert fake.is_fresh(NOW) is False


class TestJournalIsAppendOnly:
    def _idea(self) -> RecordedIdea:
        return RecordedIdea(
            idea_id=uuid4(),
            cycle_id=uuid4(),
            symbol="RELIANCE",
            setup_id="SETUP_A",
            direction=Direction.BULLISH,
            rank=1,
            confidence=0.7,
            gate_values={"iv_rank": "42"},
            hypothetical_risk=Money.from_rupees(Decimal("2500")),
            created_at=NOW,
        )

    def test_the_same_idea_cannot_be_recorded_twice(self) -> None:
        journal = FakeJournal()
        idea = self._idea()
        journal.record_idea(idea)

        with pytest.raises(ValueError, match="append-only"):
            journal.record_idea(idea)

    def test_ideas_awaiting_decision_excludes_decided_ones(self) -> None:
        journal = FakeJournal()
        first, second = self._idea(), self._idea()
        journal.record_idea(first)
        journal.record_idea(second)
        journal.record_decision(
            RecordedDecision(
                idea_id=first.idea_id, action="TOOK", reason="liked the setup", decided_at=NOW
            )
        )

        awaiting = journal.ideas_awaiting_decision(NOW)

        assert [idea.idea_id for idea in awaiting] == [second.idea_id]

    def test_a_second_decision_is_a_correction_not_an_error(self) -> None:
        """Changing your mind is expected, and the history of changing it is
        itself evidence. The latest row wins; neither is overwritten."""
        journal = FakeJournal()
        idea = self._idea()
        journal.record_idea(idea)

        journal.record_decision(
            RecordedDecision(
                idea_id=idea.idea_id, action="SKIPPED", reason="spread too wide", decided_at=NOW
            )
        )
        journal.record_decision(
            RecordedDecision(
                idea_id=idea.idea_id, action="TOOK", reason="spread tightened", decided_at=NOW
            )
        )

        latest = journal.latest_decision(idea.idea_id)
        assert latest is not None
        assert latest.action == "TOOK"
        assert len(journal.decisions) == 2  # both rows kept

    def test_a_decision_without_a_reason_is_refused(self) -> None:
        """docs/06 calls the reason the most valuable field in the schema."""
        with pytest.raises(ValueError, match="reason"):
            RecordedDecision(idea_id=uuid4(), action="SKIPPED", reason="   ", decided_at=NOW)


class TestFakeDataFiltering:
    def test_bars_are_filtered_to_the_requested_window(self) -> None:
        fake = FakeMarketData()
        instrument = _reliance()
        price = Price.from_rupees(Decimal("1400"))
        bars = [
            Bar(
                instrument=instrument,
                start=NOW + timedelta(minutes=offset),
                open=price,
                high=price,
                low=price,
                close=price,
                volume=100,
            )
            for offset in (0, 5, 10)
        ]
        fake.add_bars(instrument, bars)

        window = fake.fetch_bars(instrument, "5m", NOW, NOW + timedelta(minutes=10))

        assert len(window) == 2

    def test_news_is_filtered_by_symbol_and_time(self) -> None:
        fake = FakeNews()
        fake.add(
            NewsArticle(
                article_id="1",
                headline="Reliance wins order",
                body="full body",
                url="https://example.com/1",
                publisher="Example",
                source_tier=SourceTier.TIER_1,
                published_at=NOW,
                ingested_at=NOW,
                instrument_symbols=("RELIANCE",),
            )
        )

        assert len(fake.fetch_since(NOW - timedelta(hours=1), ["RELIANCE"])) == 1
        assert fake.fetch_since(NOW - timedelta(hours=1), ["TCS"]) == []
        assert fake.fetch_since(NOW + timedelta(hours=1), ["RELIANCE"]) == []


class TestNetworkIsBlocked:
    def test_an_outbound_connection_raises(self) -> None:
        """The guard that makes 'the suite never touches the network' true."""
        with pytest.raises(Exception, match="Use a fake"):
            socket.create_connection(("example.com", 80), timeout=1)

    def test_quote_spread_is_computed_without_a_feed(self) -> None:
        quote = Quote(
            instrument=_reliance(),
            last=Price.from_rupees(Decimal("1400")),
            bid=Price.from_rupees(Decimal("1399")),
            ask=Price.from_rupees(Decimal("1401")),
            exchange_time=NOW,
            received_at=NOW,
        )

        assert round(quote.spread_pct_of_mid(), 4) == pytest.approx(0.1429, abs=0.001)


class TestNotifierRecordsAlerts:
    def test_alerts_are_captured_with_their_level(self) -> None:
        fake = FakeNotifier()

        fake.send_alert(AlertLevel.CRITICAL, "feed down")

        assert fake.alerts == [(AlertLevel.CRITICAL, "feed down")]
