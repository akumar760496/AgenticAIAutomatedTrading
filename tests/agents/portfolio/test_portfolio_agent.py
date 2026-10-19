"""The Portfolio Context agent.

The central test is `test_one_unpriceable_option_makes_the_whole_net_delta_none`.
Everything else here is a sum; that one is the design decision, and it is the
difference between a limit check that works and one that silently checks the
wrong number.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

from tests.agents.fixtures import NIFTY, RELIANCE, nifty_chain
from tests.fakes import FakeBrokerRead, FakeJournal, FakeOptionChain
from trading_desk.agents.portfolio.agent import SECTOR_MAP_MISSING, PortfolioAgent
from trading_desk.domain.enums import Direction, OptionRight
from trading_desk.domain.instruments import InstrumentRef
from trading_desk.domain.values import Money, Price, Quantity
from trading_desk.ports.broker import BrokerPosition
from trading_desk.ports.journal import RecordedIdea

NOW = datetime(2026, 10, 19, 10, 0, tzinfo=UTC)
EXPIRY = date(2026, 10, 26)
INDEX_SYMBOLS = frozenset({"NIFTY", "BANKNIFTY"})


def _equity(symbol: str, units: int, price: str) -> BrokerPosition:
    instrument = InstrumentRef(
        symbol=symbol,
        exchange=RELIANCE.exchange,
        segment=RELIANCE.segment,
        security_id="2885",
    )
    return BrokerPosition(
        instrument=instrument,
        quantity=Quantity(units=units),
        average_price=Price.from_rupees(Decimal(price)),
        unrealised_pnl=Money.zero(),
        fetched_at=NOW,
    )


def _option(strike: str, units: int, *, right: OptionRight = OptionRight.CALL) -> BrokerPosition:
    instrument = InstrumentRef(
        symbol="NIFTY",
        exchange=NIFTY.exchange,
        segment=NIFTY.segment,
        security_id="26000",
        lot_size=75,
        expiry=EXPIRY,
        strike=Price.from_rupees(Decimal(strike)),
        right=right,
    )
    return BrokerPosition(
        instrument=instrument,
        quantity=Quantity(units=units),
        average_price=Price.from_rupees(Decimal("265")),
        unrealised_pnl=Money.zero(),
        fetched_at=NOW,
    )


def _idea(symbol: str) -> RecordedIdea:
    return RecordedIdea(
        idea_id=uuid4(),
        cycle_id=uuid4(),
        symbol=symbol,
        setup_id="SETUP_A",
        direction=Direction.BULLISH,
        rank=1,
        confidence=0.6,
        gate_values={},
        hypothetical_risk=Money.from_rupees(Decimal("5000")),
        created_at=NOW,
    )


def _agent(
    positions: list[BrokerPosition], ideas: list[RecordedIdea] | None = None
) -> PortfolioAgent:
    broker = FakeBrokerRead()
    broker.positions = positions
    broker.funds = Money.from_rupees(Decimal("500000"))

    journal = FakeJournal()
    for idea in ideas or []:
        journal.record_idea(idea)

    chains = FakeOptionChain()
    chains.add_chain(nifty_chain(EXPIRY), EXPIRY)
    return PortfolioAgent(broker, journal, chains, INDEX_SYMBOLS)


class TestNetDelta:
    def test_an_equity_position_has_a_delta_of_one_per_share(self) -> None:
        view = _agent([_equity("RELIANCE", 100, "1400")]).assess("RELIANCE", NOW)

        assert view.net_delta == 100.0

    def test_an_option_delta_is_read_from_the_chain(self) -> None:
        """24000 calls are quoted at 0.52 in the fixture chain, so 75 of them
        are 39 share-equivalents."""
        view = _agent([_option("24000", 75)]).assess("NIFTY", NOW)

        assert view.net_delta == 0.52 * 75

    def test_one_unpriceable_option_makes_the_whole_net_delta_none(self) -> None:
        """Not a partial sum. The 24050 strike is not listed in the chain, so
        the portfolio's delta is unknown - and a number computed from the rest
        would pass a limit check it should not pass (rule 6)."""
        view = _agent([_equity("RELIANCE", 100, "1400"), _option("24050", 75)]).assess("NIFTY", NOW)

        assert view.net_delta is None
        assert any("no chain quote" in item for item in view.finding.unavailable)
        assert view.finding.measurements["net_delta"] == "unavailable"

    def test_no_positions_is_a_net_delta_of_zero_not_unknown(self) -> None:
        """Flat is a known state. Only a position we cannot price is unknown."""
        view = _agent([]).assess("RELIANCE", NOW)

        assert view.net_delta == 0.0


class TestExposureAndConcentration:
    def test_exposure_is_summed_per_symbol(self) -> None:
        view = _agent([_equity("RELIANCE", 100, "1400"), _equity("RELIANCE", 50, "1400")]).assess(
            "RELIANCE", NOW
        )

        assert view.exposure_for("RELIANCE") == Money.from_rupees(Decimal("210000"))

    def test_a_symbol_not_held_has_zero_exposure(self) -> None:
        view = _agent([_equity("INFY", 10, "1500")]).assess("RELIANCE", NOW)

        assert view.exposure_for("RELIANCE") == Money.zero()

    def test_sector_concentration_is_reported_as_missing_on_every_call(self) -> None:
        """docs/02 asks for it; no sector map exists anywhere in config/, and
        writing one from memory is exactly the invented data these rules keep
        out (ISS-027)."""
        view = _agent([_equity("RELIANCE", 100, "1400")]).assess("RELIANCE", NOW)

        assert SECTOR_MAP_MISSING in view.finding.unavailable


class TestIndexPositionsAndTheses:
    def test_index_positions_are_counted_from_the_symbol_list(self) -> None:
        """Feeds Setup B's one-index-position-at-a-time gate. Counting by
        segment instead would count stock options as index positions."""
        view = _agent([_option("24000", 75), _equity("RELIANCE", 100, "1400")]).assess("NIFTY", NOW)

        assert view.open_index_positions == 1

    def test_an_open_undecided_thesis_is_surfaced(self) -> None:
        view = _agent([], ideas=[_idea("RELIANCE")]).assess("RELIANCE", NOW)

        assert view.already_has_a_thesis_on("RELIANCE")
        assert any("already open" in note for note in view.finding.notes)

    def test_a_thesis_on_another_symbol_is_counted_but_not_flagged(self) -> None:
        view = _agent([], ideas=[_idea("INFY")]).assess("RELIANCE", NOW)

        assert view.finding.measurements["open_theses"] == "1"
        assert not view.already_has_a_thesis_on("RELIANCE")


def test_the_agent_reports_and_never_vetoes() -> None:
    """Holding something is context, not a prohibition. Whether an exposure is
    too large is the risk gate's arithmetic, not this agent's opinion."""
    view = _agent([_equity("RELIANCE", 10_000, "1400")]).assess("RELIANCE", NOW)

    assert view.finding.vetoes == ()
