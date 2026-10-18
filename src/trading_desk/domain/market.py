"""Market data as the domain sees it: bars, quotes and option chains.

These are what the ports return. A vendor's JSON never reaches application code -
the adapter maps it here first, so when Dhan renames a field exactly one file
changes.

Every type carries both exchange_time (when the exchange says it happened) and
received_at (when we got it). The gap between them is how late our data is, and
the freshness gate in step 1.3 reads it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from trading_desk.domain.enums import OptionRight
from trading_desk.domain.instruments import InstrumentRef
from trading_desk.domain.values import Price


@dataclass(frozen=True)
class Bar:
    """One OHLCV candle. Interval is on the series, not the bar."""

    instrument: InstrumentRef
    start: datetime  # tz-aware UTC, the bar's opening instant
    open: Price
    high: Price
    low: Price
    close: Price
    volume: int

    def __post_init__(self) -> None:
        if self.start.tzinfo is None:
            raise ValueError(f"{self.instrument.symbol}: bar start must be tz-aware")
        if self.high < self.low:
            raise ValueError(f"{self.instrument.symbol}: high {self.high} is below low {self.low}")
        if self.volume < 0:
            raise ValueError(f"{self.instrument.symbol}: volume cannot be negative")


@dataclass(frozen=True)
class Tick:
    """One trade printed by the exchange.

    `quantity` is the size of **this trade**, not the day's running total. Feeds
    differ on which they send, and the difference is invisible until volumes come
    out roughly N times too large. Converting is the adapter's job, so that the
    bar builder can only ever be handed the incremental form.

    `sequence` is the feed's own ordering number when it provides one. It is the
    only sound basis for discarding a duplicate: two genuine trades can share a
    timestamp, a price and a size, so without a sequence number a repeat is
    indistinguishable from a real second trade and must be kept.
    """

    instrument: InstrumentRef
    price: Price
    quantity: int
    exchange_time: datetime
    sequence: int | None = None

    def __post_init__(self) -> None:
        if self.exchange_time.tzinfo is None:
            raise ValueError(f"{self.instrument.symbol}: tick time must be tz-aware")
        if self.quantity < 0:
            raise ValueError(f"{self.instrument.symbol}: quantity cannot be negative")


@dataclass(frozen=True)
class Quote:
    """A snapshot of the top of book."""

    instrument: InstrumentRef
    last: Price
    bid: Price
    ask: Price
    exchange_time: datetime
    received_at: datetime

    def __post_init__(self) -> None:
        if self.ask < self.bid:
            raise ValueError(f"{self.instrument.symbol}: ask {self.ask} is below bid {self.bid}")

    def spread_pct_of_mid(self) -> float:
        """Liquidity gate input. Setup A rejects anything wider than 3% of mid."""
        mid_paise = (self.bid.paise + self.ask.paise) / 2
        if mid_paise == 0:
            raise ValueError(f"{self.instrument.symbol}: mid price is zero")
        return (self.ask.paise - self.bid.paise) / mid_paise * 100


@dataclass(frozen=True)
class OptionQuote:
    """One leg of a chain: a single strike and right, with its greeks.

    Greeks are floats, unlike prices. They are model outputs with no exact decimal
    value, and no money is computed directly from them.
    """

    right: OptionRight
    last: Price
    bid: Price
    ask: Price
    volume: int
    open_interest: int
    implied_volatility: float | None = None
    delta: float | None = None
    gamma: float | None = None
    theta: float | None = None
    vega: float | None = None


@dataclass(frozen=True)
class OptionStrike:
    """One strike, with its call and put. Either side may be missing from a feed."""

    strike: Price
    call: OptionQuote | None
    put: OptionQuote | None


@dataclass(frozen=True)
class OptionChainSnapshot:
    """A full chain for one underlying and expiry, at one instant.

    This is the thing step 1.1 writes to Parquet three times a day, and the series
    that becomes the IV-rank history. It cannot be reconstructed later, which is why
    it is captured before any strategy exists.
    """

    underlying: InstrumentRef
    expiry_date: str  # ISO date; a str because it is an identifier here, not a calculation
    strikes: tuple[OptionStrike, ...]
    spot: Price
    exchange_time: datetime
    received_at: datetime

    def __post_init__(self) -> None:
        if not self.strikes:
            raise ValueError(f"{self.underlying.symbol}: a chain with no strikes is not a chain")
        if self.received_at.tzinfo is None or self.exchange_time.tzinfo is None:
            raise ValueError(f"{self.underlying.symbol}: timestamps must be tz-aware")

    def atm_strike(self) -> OptionStrike:
        """The strike closest to spot. The ATM IV series is built from this."""
        return min(self.strikes, key=lambda s: abs(s.strike.paise - self.spot.paise))
