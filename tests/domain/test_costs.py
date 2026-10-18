"""The cost model, with every number worked out by hand in the comment."""

from __future__ import annotations

from decimal import Decimal

import pytest

from trading_desk.domain.costs import CostRates, leg_cost, round_trip_cost
from trading_desk.domain.proposals import LegAction
from trading_desk.domain.values import Money

RATES = CostRates(
    brokerage_per_order=Money.from_rupees(Decimal("20")),
    stt_sell_fraction=Decimal("0.001"),
    exchange_txn_fraction=Decimal("0.00035"),
    sebi_fraction=Decimal("0.000001"),
    stamp_duty_buy_fraction=Decimal("0.00003"),
    gst_fraction=Decimal("0.18"),
    slippage_fraction=Decimal("0.005"),
)

# One lot of NIFTY at a 100-rupee premium: turnover 100 x 75 = 7,500.
PREMIUM = Money.from_rupees(Decimal("100"))
QUANTITY = 75
TURNOVER = Money.from_rupees(Decimal("7500"))


class TestLegCosts:
    def test_turnover_is_premium_times_quantity(self) -> None:
        assert leg_cost(PREMIUM, QUANTITY, LegAction.BUY, RATES).turnover == TURNOVER

    def test_stt_is_charged_on_the_sell_leg_only(self) -> None:
        """The single biggest asymmetry between a debit and a credit structure."""
        buy = leg_cost(PREMIUM, QUANTITY, LegAction.BUY, RATES)
        sell = leg_cost(PREMIUM, QUANTITY, LegAction.SELL, RATES)

        assert buy.stt == Money.zero()
        # 7,500 x 0.001 = 7.50
        assert sell.stt == Money.from_rupees(Decimal("7.50"))

    def test_stamp_duty_is_charged_on_the_buy_leg_only(self) -> None:
        buy = leg_cost(PREMIUM, QUANTITY, LegAction.BUY, RATES)
        sell = leg_cost(PREMIUM, QUANTITY, LegAction.SELL, RATES)

        # 7,500 x 0.00003 = 0.225 -> 23 paise (half up from 22.5)
        assert buy.stamp_duty == Money(paise=23)
        assert sell.stamp_duty == Money.zero()

    def test_gst_applies_to_brokerage_and_exchange_charge_only(self) -> None:
        """Not to statutory taxes. Applying it to everything overstates the
        cost; applying it to brokerage alone understates it."""
        cost = leg_cost(PREMIUM, QUANTITY, LegAction.BUY, RATES)

        # Exchange: 7,500 x 0.00035 = 2.625 -> 263 paise (half up).
        # GST base: 2,000 + 263 = 2,263 paise. x 0.18 = 407.34 -> 407 paise.
        assert cost.exchange_txn == Money(paise=263)
        assert cost.gst == Money(paise=407)

    def test_the_total_is_the_sum_of_its_parts(self) -> None:
        cost = leg_cost(PREMIUM, QUANTITY, LegAction.SELL, RATES)
        parts = (
            cost.brokerage.paise
            + cost.stt.paise
            + cost.exchange_txn.paise
            + cost.sebi.paise
            + cost.stamp_duty.paise
            + cost.gst.paise
            + cost.slippage.paise
        )

        assert cost.total.paise == parts

    def test_slippage_is_separate_from_the_statutory_charges(self) -> None:
        """So it can be retuned against real fills without touching the tax
        arithmetic."""
        cost = leg_cost(PREMIUM, QUANTITY, LegAction.BUY, RATES)
        # 7,500 x 0.005 = 37.50
        assert cost.slippage == Money.from_rupees(Decimal("37.50"))

    def test_a_non_positive_quantity_raises(self) -> None:
        with pytest.raises(ValueError, match="quantity must be positive"):
            leg_cost(PREMIUM, 0, LegAction.BUY, RATES)


class TestRoundTrip:
    def test_a_vertical_round_trip_is_four_orders(self) -> None:
        """Two legs in, two legs out. Charging per structure rather than per
        order would halve the brokerage and flatter the strategy."""
        trade = round_trip_cost(
            ((PREMIUM, LegAction.BUY), (PREMIUM, LegAction.SELL)), QUANTITY, RATES
        )

        assert len(trade.legs) == 4
        assert trade.itemised()["brokerage"] == Money.from_rupees(Decimal("80"))

    def test_stt_is_paid_twice_on_a_round_trip(self) -> None:
        """Each leg sells once - the short on entry, the long on exit."""
        trade = round_trip_cost(
            ((PREMIUM, LegAction.BUY), (PREMIUM, LegAction.SELL)), QUANTITY, RATES
        )

        assert trade.itemised()["stt"] == Money.from_rupees(Decimal("15"))

    def test_costs_are_material_against_a_small_spread(self) -> None:
        """A sanity check with teeth: on a 7,500-rupee turnover per leg, the
        round trip costs enough to matter against a typical spread's max gain.
        If this ever reads as negligible, the model has broken."""
        trade = round_trip_cost(
            ((PREMIUM, LegAction.BUY), (PREMIUM, LegAction.SELL)), QUANTITY, RATES
        )

        assert trade.total.rupees() > Decimal("100")
