"""Instrument identity: what we are looking at, and what the exchange calls it.

One type for equities and options. The option fields are all-or-nothing: an
instrument either has expiry, strike and right, or it has none of them. A
half-specified option is the kind of value that produces an order for the wrong
contract, so it cannot be constructed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from trading_desk.domain.enums import Exchange, OptionRight, Segment
from trading_desk.domain.values import Price


@dataclass(frozen=True)
class InstrumentRef:
    """A tradeable instrument, as both we and the broker identify it.

    security_id is the broker's identifier. It is a str because Dhan returns it as
    one, and converting an identifier to an int is how leading zeros disappear.
    """

    symbol: str
    exchange: Exchange
    segment: Segment
    security_id: str
    lot_size: int = 1

    # Options only. All three are set together, or none are.
    expiry: date | None = None
    strike: Price | None = None
    right: OptionRight | None = None

    def __post_init__(self) -> None:
        if not self.symbol:
            raise ValueError("symbol must not be empty")
        if not self.security_id:
            raise ValueError(f"{self.symbol}: security_id must not be empty")
        if self.lot_size <= 0:
            raise ValueError(f"{self.symbol}: lot_size must be > 0, got {self.lot_size}")

        option_fields = (self.expiry, self.strike, self.right)
        some_set = any(field is not None for field in option_fields)
        all_set = all(field is not None for field in option_fields)
        if some_set and not all_set:
            raise ValueError(
                f"{self.symbol}: an option needs expiry, strike and right together. "
                f"Got expiry={self.expiry}, strike={self.strike}, right={self.right}"
            )

    @property
    def is_option(self) -> bool:
        return self.right is not None

    def days_to_expiry(self, as_of: date) -> int:
        """Calendar days to expiry. Takes the date in - the domain does not read a clock."""
        if self.expiry is None:
            raise ValueError(f"{self.symbol} is not an option and has no expiry")
        return (self.expiry - as_of).days

    def __str__(self) -> str:
        strike, right = self.strike, self.right
        if strike is None or right is None:
            return f"{self.symbol}@{self.exchange}"
        # Readable in a briefing: NIFTY 2026-10-20 25000 CE
        short_right = "CE" if right is OptionRight.CALL else "PE"
        return f"{self.symbol} {self.expiry} {strike.rupees():g} {short_right}"
