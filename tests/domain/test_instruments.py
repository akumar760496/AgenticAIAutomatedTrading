"""InstrumentRef identifies one thing precisely, or refuses to exist."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import date
from decimal import Decimal

import pytest

from trading_desk.domain.enums import Exchange, OptionRight, Segment
from trading_desk.domain.instruments import InstrumentRef
from trading_desk.domain.values import Price


def _equity() -> InstrumentRef:
    return InstrumentRef(
        symbol="RELIANCE",
        exchange=Exchange.NSE,
        segment=Segment.EQUITY,
        security_id="2885",
    )


def _nifty_call() -> InstrumentRef:
    return InstrumentRef(
        symbol="NIFTY",
        exchange=Exchange.NSE,
        segment=Segment.FNO,
        security_id="43925",
        lot_size=65,  # Jan 2026 series, from config in step 1.2
        expiry=date(2026, 10, 20),  # a Tuesday - NSE weekly expiry
        strike=Price.from_rupees(Decimal("25000")),
        right=OptionRight.CALL,
    )


def test_equity_is_not_an_option() -> None:
    assert _equity().is_option is False


def test_option_is_an_option() -> None:
    assert _nifty_call().is_option is True


def test_half_specified_option_cannot_be_constructed() -> None:
    """Expiry without strike and right would silently identify the wrong contract."""
    with pytest.raises(ValueError, match="together"):
        InstrumentRef(
            symbol="NIFTY",
            exchange=Exchange.NSE,
            segment=Segment.FNO,
            security_id="43925",
            expiry=date(2026, 10, 20),
        )


def test_empty_symbol_is_rejected() -> None:
    with pytest.raises(ValueError, match="symbol"):
        InstrumentRef(symbol="", exchange=Exchange.NSE, segment=Segment.EQUITY, security_id="1")


def test_empty_security_id_is_rejected() -> None:
    with pytest.raises(ValueError, match="security_id"):
        InstrumentRef(
            symbol="RELIANCE", exchange=Exchange.NSE, segment=Segment.EQUITY, security_id=""
        )


def test_zero_lot_size_is_rejected() -> None:
    with pytest.raises(ValueError, match="lot_size"):
        InstrumentRef(
            symbol="NIFTY",
            exchange=Exchange.NSE,
            segment=Segment.FNO,
            security_id="43925",
            lot_size=0,
        )


def test_days_to_expiry_takes_the_date_as_an_argument() -> None:
    """The domain never reads a clock. The caller supplies 'today'."""
    assert _nifty_call().days_to_expiry(as_of=date(2026, 10, 15)) == 5


def test_days_to_expiry_on_an_equity_is_an_error() -> None:
    with pytest.raises(ValueError, match="not an option"):
        _equity().days_to_expiry(as_of=date(2026, 10, 15))


def test_option_renders_readably_for_a_briefing() -> None:
    assert str(_nifty_call()) == "NIFTY 2026-10-20 25000 CE"


def test_is_frozen() -> None:
    instrument = _equity()

    with pytest.raises(FrozenInstanceError):
        instrument.symbol = "TCS"  # type: ignore[misc]
