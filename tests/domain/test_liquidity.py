"""Liquidity measurement and ranking.

The cases that matter are the ones where a chain looks liquid and is not, because
those are the names that reach a briefing and cannot be traded.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from trading_desk.domain.enums import Exchange, OptionRight, Segment
from trading_desk.domain.instruments import InstrumentRef
from trading_desk.domain.liquidity import (
    LiquidityMeasure,
    LiquidityRules,
    measure_chain,
    rank_by_liquidity,
)
from trading_desk.domain.market import OptionChainSnapshot, OptionQuote, OptionStrike
from trading_desk.domain.values import Money, Price

NOW = datetime(2026, 10, 16, 4, 0, tzinfo=UTC)

RULES = LiquidityRules(
    min_premium_turnover=Money.from_rupees("100000"),
    min_open_interest=1000,
    min_priced_fraction=0.25,
    max_stocks=2,
)


def _instrument(symbol: str) -> InstrumentRef:
    return InstrumentRef(
        symbol=symbol,
        exchange=Exchange.NSE,
        segment=Segment.FNO,
        security_id="1",
        lot_size=50,
    )


def _quote(
    right: OptionRight, last: str, volume: int, open_interest: int, iv: float | None
) -> OptionQuote:
    return OptionQuote(
        right=right,
        last=Price.from_rupees(Decimal(last)),
        bid=Price.from_rupees(Decimal(last)),
        ask=Price.from_rupees(Decimal(last)),
        volume=volume,
        open_interest=open_interest,
        implied_volatility=iv,
    )


def _chain(symbol: str, strikes: tuple[OptionStrike, ...]) -> OptionChainSnapshot:
    return OptionChainSnapshot(
        underlying=_instrument(symbol),
        expiry_date="2026-10-20",
        strikes=strikes,
        spot=Price.from_rupees(Decimal("25000")),
        exchange_time=NOW,
        received_at=NOW,
    )


def _measure(symbol: str, turnover: str, is_index: bool = False) -> LiquidityMeasure:
    return LiquidityMeasure(
        symbol=symbol,
        is_index=is_index,
        premium_turnover=Money.from_rupees(turnover),
        open_interest=10_000,
        legs_priced=10,
        legs_total=10,
    )


class TestMeasureChain:
    def test_turnover_is_price_times_volume_over_every_leg(self) -> None:
        strike = OptionStrike(
            strike=Price.from_rupees(Decimal("25000")),
            call=_quote(OptionRight.CALL, "100", volume=10, open_interest=500, iv=13.0),
            put=_quote(OptionRight.PUT, "50", volume=20, open_interest=300, iv=14.0),
        )

        measure = measure_chain(_chain("NIFTY", (strike,)), is_index=True)

        # 100x10 + 50x20 = 2000
        assert measure.premium_turnover == Money.from_rupees("2000")
        assert measure.open_interest == 800
        assert measure.legs_total == 2
        assert measure.legs_priced == 2

    def test_a_missing_side_is_not_counted_as_a_leg(self) -> None:
        """legs_total must stay an honest denominator for priced_fraction."""
        strike = OptionStrike(
            strike=Price.from_rupees(Decimal("25000")),
            call=_quote(OptionRight.CALL, "100", volume=10, open_interest=500, iv=13.0),
            put=None,
        )

        measure = measure_chain(_chain("NIFTY", (strike,)), is_index=True)

        assert measure.legs_total == 1
        assert measure.priced_fraction == 1.0

    def test_unpriced_legs_count_in_the_total_but_not_as_priced(self) -> None:
        """The case that makes priced_fraction worth measuring at all."""
        strikes = tuple(
            OptionStrike(
                strike=Price.from_rupees(Decimal(25000 + 100 * offset)),
                call=_quote(
                    OptionRight.CALL,
                    "100" if offset == 0 else "0",
                    volume=10 if offset == 0 else 0,
                    open_interest=500,
                    iv=13.0 if offset == 0 else None,
                ),
                put=None,
            )
            for offset in range(4)
        )

        measure = measure_chain(_chain("THINLY", strikes), is_index=False)

        assert measure.legs_total == 4
        assert measure.legs_priced == 1
        assert measure.priced_fraction == pytest.approx(0.25)

    def test_zero_volume_chain_measures_zero_turnover(self) -> None:
        """A weekend survey looks exactly like this, which is why it is detected."""
        strike = OptionStrike(
            strike=Price.from_rupees(Decimal("25000")),
            call=_quote(OptionRight.CALL, "100", volume=0, open_interest=500, iv=13.0),
            put=None,
        )

        measure = measure_chain(_chain("NIFTY", (strike,)), is_index=True)

        assert measure.premium_turnover.is_zero()


class TestRanking:
    def test_ranks_by_turnover_descending(self) -> None:
        measures = (
            _measure("SMALL", "200000"),
            _measure("BIG", "900000"),
        )

        ranking = rank_by_liquidity(measures, RULES)

        assert ranking.symbols == ("BIG", "SMALL")

    def test_excludes_below_turnover_floor_with_both_numbers_in_the_reason(self) -> None:
        ranking = rank_by_liquidity((_measure("THIN", "500"),), RULES)

        assert ranking.included == ()
        assert len(ranking.excluded) == 1
        reason = ranking.excluded[0].reason
        assert "500.00" in reason  # what was measured
        assert "100,000.00" in reason  # what it was compared against

    def test_excludes_a_chain_with_turnover_but_almost_no_priced_legs(self) -> None:
        """The trap: real turnover concentrated in a couple of strikes."""
        concentrated = LiquidityMeasure(
            symbol="CONCENTRATED",
            is_index=False,
            premium_turnover=Money.from_rupees("900000"),
            open_interest=10_000,
            legs_priced=2,
            legs_total=40,
        )

        ranking = rank_by_liquidity((concentrated,), RULES)

        assert ranking.included == ()
        assert "legs priced" in ranking.excluded[0].reason

    def test_stock_cap_applies_and_names_the_dropped_stocks(self) -> None:
        measures = tuple(_measure(f"S{index}", str(900000 - index * 1000)) for index in range(4))

        ranking = rank_by_liquidity(measures, RULES)

        assert ranking.symbols == ("S0", "S1")
        capped = [item for item in ranking.excluded if "ranked below" in item.reason]
        assert {item.symbol for item in capped} == {"S2", "S3"}

    def test_indices_are_not_counted_against_the_stock_cap(self) -> None:
        measures = (
            _measure("NIFTY", "900000", is_index=True),
            _measure("BANKNIFTY", "800000", is_index=True),
            _measure("S0", "700000"),
            _measure("S1", "600000"),
            _measure("S2", "500000"),
        )

        ranking = rank_by_liquidity(measures, RULES)

        assert set(ranking.symbols) == {"NIFTY", "BANKNIFTY", "S0", "S1"}

    def test_an_index_still_has_to_clear_the_floors(self) -> None:
        """No always-include list: a failing index is a fact worth seeing."""
        ranking = rank_by_liquidity((_measure("NIFTY", "10", is_index=True),), RULES)

        assert ranking.included == ()
        assert ranking.excluded[0].symbol == "NIFTY"

    def test_every_measure_appears_either_included_or_excluded(self) -> None:
        """Nothing may vanish between the survey and the universe."""
        measures = (
            _measure("NIFTY", "900000", is_index=True),
            _measure("S0", "800000"),
            _measure("S1", "700000"),
            _measure("S2", "600000"),
            _measure("THIN", "5"),
        )

        ranking = rank_by_liquidity(measures, RULES)

        accounted = set(ranking.symbols) | {item.symbol for item in ranking.excluded}
        assert accounted == {"NIFTY", "S0", "S1", "S2", "THIN"}
