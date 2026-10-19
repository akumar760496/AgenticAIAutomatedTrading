"""Shared market fixtures for the specialist agents.

Hand-built rather than generated: a test that states its own bars can be read
against the number it asserts, and a generated series can only be checked by
running the code the test is supposed to be checking.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from trading_desk.domain.enums import Exchange, OptionRight, Segment
from trading_desk.domain.instruments import InstrumentRef
from trading_desk.domain.market import Bar, OptionChainSnapshot, OptionQuote, OptionStrike
from trading_desk.domain.values import Price

RELIANCE = InstrumentRef(
    symbol="RELIANCE",
    exchange=Exchange.NSE,
    segment=Segment.EQUITY,
    security_id="2885",
    lot_size=250,
)

NIFTY = InstrumentRef(
    symbol="NIFTY",
    exchange=Exchange.NSE,
    segment=Segment.FNO,
    security_id="26000",
    lot_size=75,
)

FIRST_BAR_DAY = date(2026, 6, 1)
NOW = datetime(2026, 10, 19, 10, 0, tzinfo=UTC)


def flat_bars(count: int, *, close: str = "1400", volume: int = 100_000) -> list[Bar]:
    """`count` identical sessions. A boring baseline to perturb."""
    price = Price.from_rupees(Decimal(close))
    start = datetime.combine(FIRST_BAR_DAY, datetime.min.time(), tzinfo=UTC)
    return [
        Bar(
            instrument=RELIANCE,
            start=start + timedelta(days=index),
            open=price,
            high=price,
            low=price,
            close=price,
            volume=volume,
        )
        for index in range(count)
    ]


def with_breakout(bars: list[Bar], *, close: str, volume: int) -> list[Bar]:
    """Replace the last bar with one that closes away from the range."""
    last = bars[-1]
    price = Price.from_rupees(Decimal(close))
    replacement = Bar(
        instrument=last.instrument,
        start=last.start,
        open=last.open,
        high=max(price, last.high),
        low=min(price, last.low),
        close=price,
        volume=volume,
    )
    return [*bars[:-1], replacement]


def _quote(right: OptionRight, mid: str, delta: float, *, spread: str = "1") -> OptionQuote:
    centre = Decimal(mid)
    half = Decimal(spread) / 2
    return OptionQuote(
        right=right,
        last=Price.from_rupees(centre),
        bid=Price.from_rupees(centre - half),
        ask=Price.from_rupees(centre + half),
        volume=1000,
        open_interest=5000,
        implied_volatility=18.0,
        delta=delta,
    )


def chain(underlying: InstrumentRef, expiry: date) -> OptionChainSnapshot:
    """A seven-strike chain with both delta bands satisfiable.

    Deliberately kinder than a real NSE chain, where only 10 of 25 names have a
    strike in both of Setup A's bands (ISS-021). These tests are about the
    agent's plumbing; the band problem is a separate, measured issue.
    """
    rows = [
        ("1340", "52", 0.80, "4", -0.20),
        ("1360", "42", 0.72, "6", -0.28),
        ("1380", "33", 0.62, "9", -0.38),
        ("1400", "24", 0.52, "15", -0.48),
        ("1420", "16", 0.46, "23", -0.54),
        ("1440", "10", 0.28, "34", -0.70),
        ("1460", "6", 0.18, "48", -0.78),
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
        underlying=underlying,
        expiry_date=expiry.isoformat(),
        strikes=strikes,
        spot=Price.from_rupees(Decimal("1400")),
        exchange_time=NOW,
        received_at=NOW,
    )


def nifty_chain(expiry: date) -> OptionChainSnapshot:
    """A nine-strike index chain, wide enough that a credit spread has a wing.

    The seven-strike stock chain above deliberately has none below its lowest
    strike, which is how a real thin chain behaves and is tested separately.
    """
    rows = [
        ("23200", "860", 0.92, "18", -0.08),
        ("23400", "690", 0.88, "28", -0.12),
        ("23600", "530", 0.82, "45", -0.18),
        ("23800", "390", 0.70, "78", -0.30),
        ("24000", "265", 0.52, "150", -0.48),
        ("24200", "170", 0.40, "255", -0.60),
        ("24400", "105", 0.28, "390", -0.72),
        ("24600", "60", 0.18, "545", -0.82),
        ("24800", "32", 0.10, "715", -0.90),
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
        underlying=NIFTY,
        expiry_date=expiry.isoformat(),
        strikes=strikes,
        spot=Price.from_rupees(Decimal("24000")),
        exchange_time=NOW,
        received_at=NOW,
    )
