"""Value objects for money, prices, quantities and percentages.

The rule this file exists to enforce: money is never a float. 0.1 + 0.2 is
0.30000000000000004, and a position size computed from that is wrong in a way no
test will catch until it matters. Money is an integer number of paise.

All types here are frozen. A Money that can be mutated is a Money that will be,
somewhere, in a loop, at 3pm.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

PAISE_PER_RUPEE = 100


def _to_decimal(value: Decimal | int | str, field: str) -> Decimal:
    """Accept Decimal, int or str. Reject float loudly - that is the whole point."""
    if isinstance(value, float):
        raise TypeError(
            f"{field} must not be built from a float ({value!r}). "
            f'Use Decimal("{value}") or an int. Floats lose money.'
        )
    return Decimal(value)


@dataclass(frozen=True, order=True)
class Money:
    """An amount in paise. Use from_rupees() to build, rupees() to display."""

    paise: int

    def __post_init__(self) -> None:
        if not isinstance(self.paise, int) or isinstance(self.paise, bool):
            raise TypeError(f"Money.paise must be an int, got {type(self.paise).__name__}")

    @classmethod
    def from_rupees(cls, rupees: Decimal | int | str) -> Money:
        amount = _to_decimal(rupees, "Money")
        return cls(paise=int((amount * PAISE_PER_RUPEE).to_integral_value(ROUND_HALF_UP)))

    @classmethod
    def zero(cls) -> Money:
        return cls(paise=0)

    def rupees(self) -> Decimal:
        return Decimal(self.paise) / PAISE_PER_RUPEE

    def is_zero(self) -> bool:
        return self.paise == 0

    def __add__(self, other: Money) -> Money:
        return Money(self.paise + other.paise)

    def __sub__(self, other: Money) -> Money:
        return Money(self.paise - other.paise)

    def __mul__(self, factor: int) -> Money:
        """Multiply by a whole number - a lot count, a quantity. Never by a float."""
        if not isinstance(factor, int) or isinstance(factor, bool):
            raise TypeError(f"Multiply Money by an int, not {type(factor).__name__}")
        return Money(self.paise * factor)

    def __neg__(self) -> Money:
        return Money(-self.paise)

    def __str__(self) -> str:
        return f"₹{self.rupees():,.2f}"


@dataclass(frozen=True, order=True)
class Price:
    """A quoted price, in paise. Separate from Money so the two cannot be confused.

    A price is per unit; money is an amount. Multiplying a Price by a Quantity gives
    Money, and that is the only way to get there.
    """

    paise: int

    def __post_init__(self) -> None:
        if not isinstance(self.paise, int) or isinstance(self.paise, bool):
            raise TypeError(f"Price.paise must be an int, got {type(self.paise).__name__}")
        if self.paise < 0:
            raise ValueError(f"Price cannot be negative, got {self.paise} paise")

    @classmethod
    def from_rupees(cls, rupees: Decimal | int | str) -> Price:
        amount = _to_decimal(rupees, "Price")
        return cls(paise=int((amount * PAISE_PER_RUPEE).to_integral_value(ROUND_HALF_UP)))

    def rupees(self) -> Decimal:
        return Decimal(self.paise) / PAISE_PER_RUPEE

    def times(self, quantity: Quantity) -> Money:
        """Price x Quantity = Money. The only bridge between the two types."""
        return Money(self.paise * quantity.units)

    def __str__(self) -> str:
        return f"₹{self.rupees():,.2f}"


@dataclass(frozen=True, order=True)
class Quantity:
    """A number of units (shares, or contracts x lot size). Always whole."""

    units: int

    def __post_init__(self) -> None:
        if not isinstance(self.units, int) or isinstance(self.units, bool):
            raise TypeError(f"Quantity.units must be an int, got {type(self.units).__name__}")
        if self.units < 0:
            raise ValueError(f"Quantity cannot be negative, got {self.units}")

    @classmethod
    def from_lots(cls, lots: int, lot_size: int) -> Quantity:
        """Options and futures trade in lots. Lot size is config, never a literal."""
        if lots < 0 or lot_size <= 0:
            raise ValueError(f"lots must be >= 0 and lot_size > 0, got {lots} and {lot_size}")
        return cls(units=lots * lot_size)

    def __str__(self) -> str:
        return str(self.units)


@dataclass(frozen=True, order=True)
class Percent:
    """A percentage, stored as a Decimal in percent units: Percent("1.5") is 1.5%.

    Stored as percent rather than a fraction because every threshold in docs/06 is
    written in percent, and converting at the boundary is where sign and scale errors
    come from.
    """

    value: Decimal

    def __post_init__(self) -> None:
        # Typed as Decimal, but callers are not always typed. The annotation does not
        # run; this does. Widened to object so the check is not optimised away.
        value: object = self.value
        if isinstance(value, float):
            raise TypeError('Percent must not be built from a float. Use Decimal("1.5").')
        if not isinstance(value, Decimal):
            raise TypeError(f"Percent.value must be a Decimal, got {type(value).__name__}")

    @classmethod
    def from_fraction(cls, fraction: Decimal) -> Percent:
        """0.0025 -> 0.25%. Used where a ratio is computed rather than quoted."""
        return cls(value=_to_decimal(fraction, "Percent") * 100)

    def as_fraction(self) -> Decimal:
        return self.value / 100

    def of(self, amount: Money) -> Money:
        """0.25% of ₹10,00,000 = ₹2,500. Rounds to the nearest paisa."""
        paise = (Decimal(amount.paise) * self.as_fraction()).to_integral_value(ROUND_HALF_UP)
        return Money(int(paise))

    def __str__(self) -> str:
        return f"{self.value}%"
