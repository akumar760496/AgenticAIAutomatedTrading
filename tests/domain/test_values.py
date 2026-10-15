"""Money, Price, Quantity and Percent behave, and refuse floats."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from trading_desk.domain.values import Money, Percent, Price, Quantity


class TestMoney:
    def test_built_from_a_decimal_string(self) -> None:
        assert Money.from_rupees(Decimal("10.50")).paise == 1050

    def test_a_float_is_rejected_with_a_useful_message(self) -> None:
        """The rule this whole module exists for."""
        with pytest.raises(TypeError) as caught:
            Money.from_rupees(10.5)  # type: ignore[arg-type]

        assert "float" in str(caught.value)
        assert "Decimal" in str(caught.value)

    def test_the_classic_float_error_does_not_happen(self) -> None:
        # 0.1 + 0.2 == 0.30000000000000004 as floats. Not here.
        total = Money.from_rupees(Decimal("0.10")) + Money.from_rupees(Decimal("0.20"))

        assert total.paise == 30
        assert total.rupees() == Decimal("0.30")

    def test_rounding_is_half_up_not_bankers(self) -> None:
        """Python rounds 0.5 to even by default. For money that surprises people."""
        assert Money.from_rupees(Decimal("0.005")).paise == 1
        assert Money.from_rupees(Decimal("0.015")).paise == 2

    def test_arithmetic(self) -> None:
        ten = Money.from_rupees(Decimal("10"))
        three = Money.from_rupees(Decimal("3"))

        assert (ten - three).rupees() == Decimal("7")
        assert (ten * 3).rupees() == Decimal("30")
        assert (-ten).paise == -1000

    def test_multiplying_by_a_float_is_rejected(self) -> None:
        with pytest.raises(TypeError):
            Money.from_rupees(Decimal("10")) * 1.5  # type: ignore[operator]

    def test_can_be_negative_because_losses_exist(self) -> None:
        assert (Money.zero() - Money.from_rupees(Decimal("5"))).paise == -500

    def test_is_frozen(self) -> None:
        amount = Money.from_rupees(Decimal("10"))

        with pytest.raises(FrozenInstanceError):
            amount.paise = 999  # type: ignore[misc]

    def test_displays_as_rupees(self) -> None:
        assert str(Money.from_rupees(Decimal("123456.70"))) == "₹123,456.70"

    def test_sorts_by_amount(self) -> None:
        amounts = [Money(300), Money(100), Money(200)]

        assert sorted(amounts) == [Money(100), Money(200), Money(300)]


class TestPrice:
    def test_cannot_be_negative(self) -> None:
        """A quote below zero is a parsing bug, not a market condition."""
        with pytest.raises(ValueError, match="negative"):
            Price(-1)

    def test_times_quantity_gives_money(self) -> None:
        premium = Price.from_rupees(Decimal("125.50"))
        quantity = Quantity.from_lots(lots=2, lot_size=65)  # NIFTY, Jan 2026 series

        total = premium.times(quantity)

        assert total.rupees() == Decimal("16315.00")

    def test_a_float_is_rejected(self) -> None:
        with pytest.raises(TypeError):
            Price.from_rupees(125.5)  # type: ignore[arg-type]


class TestQuantity:
    def test_from_lots_multiplies_by_lot_size(self) -> None:
        assert Quantity.from_lots(lots=3, lot_size=30).units == 90  # BANKNIFTY

    def test_cannot_be_negative(self) -> None:
        with pytest.raises(ValueError, match="negative"):
            Quantity(-1)

    def test_zero_lot_size_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="lot_size"):
            Quantity.from_lots(lots=1, lot_size=0)


class TestPercent:
    def test_of_an_amount(self) -> None:
        """0.25% of 10 lakh - the v1 risk-per-trade calculation."""
        equity = Money.from_rupees(Decimal("1000000"))

        risk = Percent(Decimal("0.25")).of(equity)

        assert risk.rupees() == Decimal("2500.00")

    def test_from_fraction_converts_to_percent_units(self) -> None:
        assert Percent.from_fraction(Decimal("0.0025")).value == Decimal("0.2500")

    def test_a_float_is_rejected(self) -> None:
        with pytest.raises(TypeError):
            Percent(0.25)  # type: ignore[arg-type]

    def test_displays_with_a_sign(self) -> None:
        assert str(Percent(Decimal("1.5"))) == "1.5%"
