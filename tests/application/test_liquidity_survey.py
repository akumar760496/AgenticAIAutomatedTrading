"""The survey measures what it can and refuses to rank a survey it should not trust."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from tests.fakes import FakeNotifier, FakeOptionChain, FrozenClock
from trading_desk.application.liquidity_survey import MIN_USABLE_MEASURES, LiquiditySurvey
from trading_desk.domain.enums import Exchange, OptionRight, Segment
from trading_desk.domain.instruments import InstrumentCatalogue, InstrumentRef, UnderlyingInfo
from trading_desk.domain.market import OptionChainSnapshot, OptionQuote, OptionStrike
from trading_desk.domain.values import Price
from trading_desk.ports.notifier import AlertLevel

NOW = datetime(2026, 10, 16, 4, 0, tzinfo=UTC)
EXPIRY = date(2026, 10, 19)


def _catalogue(symbols: list[str]) -> InstrumentCatalogue:
    return InstrumentCatalogue(
        underlyings={
            symbol: UnderlyingInfo(
                symbol=symbol, security_id=str(index), lot_size=50, is_index=False
            )
            for index, symbol in enumerate(symbols)
        },
        unresolved=(),
    )


def _chain(symbol: str, volume: int, exchange_time: datetime | None = None) -> OptionChainSnapshot:
    quote = OptionQuote(
        right=OptionRight.CALL,
        last=Price.from_rupees(Decimal("100")),
        bid=Price.from_rupees(Decimal("99")),
        ask=Price.from_rupees(Decimal("101")),
        volume=volume,
        open_interest=5000,
        implied_volatility=13.5,
    )
    return OptionChainSnapshot(
        underlying=InstrumentRef(
            symbol=symbol,
            exchange=Exchange.NSE,
            segment=Segment.FNO,
            security_id="1",
            lot_size=50,
        ),
        expiry_date=EXPIRY.isoformat(),
        strikes=(OptionStrike(strike=Price.from_rupees(Decimal("25000")), call=quote, put=None),),
        spot=Price.from_rupees(Decimal("25000")),
        exchange_time=NOW,
        received_at=NOW,
    )


def _survey(chains: FakeOptionChain, notifier: FakeNotifier) -> LiquiditySurvey:
    return LiquiditySurvey(chain_source=chains, notifier=notifier, clock=FrozenClock(NOW))


def _symbols(count: int) -> list[str]:
    return [f"SYM{index:03d}" for index in range(count)]


def test_measures_every_underlying_in_the_catalogue() -> None:
    chains, notifier = FakeOptionChain(), FakeNotifier()
    symbols = _symbols(MIN_USABLE_MEASURES)
    for symbol in symbols:
        chains.add_chain(_chain(symbol, volume=100), EXPIRY)

    result = _survey(chains, notifier).run(_catalogue(symbols))

    assert len(result.measures) == MIN_USABLE_MEASURES
    assert result.failures == []
    assert result.unusable_reason is None
    assert result.surveyed_at == NOW


def test_one_bad_chain_does_not_end_the_survey() -> None:
    chains, notifier = FakeOptionChain(), FakeNotifier()
    symbols = _symbols(MIN_USABLE_MEASURES + 1)
    for symbol in symbols:
        chains.add_chain(_chain(symbol, volume=100), EXPIRY)
    chains.fail_next_fetch = True

    result = _survey(chains, notifier).run(_catalogue(symbols))

    assert len(result.failures) == 1
    assert "ConnectionError" in result.failures[0].error
    assert len(result.measures) == MIN_USABLE_MEASURES
    assert notifier.alerts[-1][0] is AlertLevel.WARNING


def test_an_underlying_with_no_expiries_is_recorded_not_skipped() -> None:
    chains, notifier = FakeOptionChain(), FakeNotifier()
    symbols = _symbols(MIN_USABLE_MEASURES)
    for symbol in symbols:
        chains.add_chain(_chain(symbol, volume=100), EXPIRY)

    result = _survey(chains, notifier).run(_catalogue([*symbols, "NOOPTIONS"]))

    assert [failure.symbol for failure in result.failures] == ["NOOPTIONS"]
    assert "no listed expiries" in result.failures[0].error


def test_a_zero_volume_survey_is_unusable_and_alerts_critically() -> None:
    """A weekend run. Ranking this would empty the universe."""
    chains, notifier = FakeOptionChain(), FakeNotifier()
    symbols = _symbols(MIN_USABLE_MEASURES)
    for symbol in symbols:
        chains.add_chain(_chain(symbol, volume=0), EXPIRY)

    result = _survey(chains, notifier).run(_catalogue(symbols))

    assert result.zero_turnover_count == MIN_USABLE_MEASURES
    assert result.unusable_reason is not None
    assert "zero" in result.unusable_reason
    assert notifier.alerts[-1][0] is AlertLevel.CRITICAL


def test_a_survey_of_too_few_names_is_unusable() -> None:
    chains, notifier = FakeOptionChain(), FakeNotifier()
    chains.add_chain(_chain("ONLYONE", volume=100), EXPIRY)

    result = _survey(chains, notifier).run(_catalogue(["ONLYONE"]))

    assert result.unusable_reason is not None
    assert str(MIN_USABLE_MEASURES) in result.unusable_reason


def test_underlyings_are_surveyed_in_symbol_order() -> None:
    """So an interrupted half-hour survey is reproducible."""
    chains, notifier = FakeOptionChain(), FakeNotifier()
    symbols = _symbols(MIN_USABLE_MEASURES)
    for symbol in symbols:
        chains.add_chain(_chain(symbol, volume=100), EXPIRY)

    result = _survey(chains, notifier).run(_catalogue(list(reversed(symbols))))

    assert [measure.symbol for measure in result.measures] == sorted(symbols)
