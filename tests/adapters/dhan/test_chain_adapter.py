"""The Dhan chain payload maps to a domain snapshot, and prices stay exact.

The fixture mirrors Dhan's documented response shape. When the real API disagrees
with it, this fixture is what gets corrected - and the diff shows exactly what
changed on their side.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest

from tests.fakes.clock import FrozenClock
from trading_desk.adapters.dhan.chain import DhanOptionChainAdapter
from trading_desk.domain.enums import Exchange, OptionRight, Segment
from trading_desk.domain.instruments import InstrumentRef
from trading_desk.domain.values import Price

NOW = datetime(2026, 10, 16, 6, 30, tzinfo=UTC)


def _nifty() -> InstrumentRef:
    return InstrumentRef(
        symbol="NIFTY",
        exchange=Exchange.NSE,
        segment=Segment.FNO,
        security_id="13",
        lot_size=65,
    )


def _reliance() -> InstrumentRef:
    return InstrumentRef(
        symbol="RELIANCE",
        exchange=Exchange.NSE,
        segment=Segment.FNO,
        security_id="2885",
        lot_size=500,
    )


CHAIN_RESPONSE: dict[str, Any] = {
    "status": "success",
    "data": {
        "last_price": 25123.45,
        "oc": {
            "25000.000000": {
                "ce": {
                    "last_price": 210.5,
                    "top_bid_price": 210.0,
                    "top_ask_price": 211.0,
                    "volume": 125000,
                    "oi": 48000,
                    "implied_volatility": 12.85,
                    "greeks": {"delta": 0.55, "gamma": 0.0004, "theta": -8.2, "vega": 11.4},
                },
                "pe": {
                    "last_price": 88.25,
                    "top_bid_price": 88.0,
                    "top_ask_price": 88.5,
                    "volume": 90000,
                    "oi": 61000,
                    "implied_volatility": 13.1,
                    "greeks": {"delta": -0.45, "gamma": 0.0004, "theta": -7.1, "vega": 11.2},
                },
            },
            "25100.000000": {
                # An illiquid strike: listed, but no greeks and no put side.
                "ce": {
                    "last_price": 0,
                    "top_bid_price": 0,
                    "top_ask_price": 0,
                    "volume": 0,
                    "oi": 0,
                    "implied_volatility": None,
                    "greeks": {},
                },
                "pe": None,
            },
        },
    },
}


class StubClient:
    """Returns a canned body and records what was asked for."""

    def __init__(self, body: dict[str, Any]) -> None:
        self.body = body
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def post(self, path: str, payload: dict[str, Any], rate_limited: bool = True) -> dict[str, Any]:
        self.calls.append((path, payload))
        return self.body


def _adapter(body: dict[str, Any]) -> tuple[DhanOptionChainAdapter, StubClient]:
    client = StubClient(body)
    return DhanOptionChainAdapter(client, FrozenClock(NOW)), client  # type: ignore[arg-type]


class TestChainMapping:
    def test_spot_and_strike_prices_are_exact(self) -> None:
        adapter, _ = _adapter(CHAIN_RESPONSE)

        chain = adapter.fetch_chain(_nifty(), date(2026, 10, 20))

        assert chain.spot.rupees() == Decimal("25123.45")
        assert chain.strikes[0].strike == Price.from_rupees(Decimal("25000"))

    def test_greeks_and_iv_are_carried_through(self) -> None:
        adapter, _ = _adapter(CHAIN_RESPONSE)

        call = adapter.fetch_chain(_nifty(), date(2026, 10, 20)).strikes[0].call

        assert call is not None
        assert call.right is OptionRight.CALL
        assert call.implied_volatility == 12.85
        assert call.delta == 0.55
        assert call.open_interest == 48000

    def test_missing_greeks_become_none_not_zero(self) -> None:
        """An illiquid strike has no IV. Recording 0.0 would corrupt the IV series."""
        adapter, _ = _adapter(CHAIN_RESPONSE)

        illiquid = adapter.fetch_chain(_nifty(), date(2026, 10, 20)).strikes[1]

        assert illiquid.call is not None
        assert illiquid.call.implied_volatility is None
        assert illiquid.call.delta is None

    def test_a_missing_side_is_none(self) -> None:
        adapter, _ = _adapter(CHAIN_RESPONSE)

        assert adapter.fetch_chain(_nifty(), date(2026, 10, 20)).strikes[1].put is None

    def test_strikes_are_sorted_numerically(self) -> None:
        """String-sorted strikes would put 10000 before 9000. Numeric order matters."""
        adapter, _ = _adapter(CHAIN_RESPONSE)

        strikes = adapter.fetch_chain(_nifty(), date(2026, 10, 20)).strikes

        assert [s.strike.paise for s in strikes] == sorted(s.strike.paise for s in strikes)

    def test_atm_strike_is_the_one_nearest_spot(self) -> None:
        adapter, _ = _adapter(CHAIN_RESPONSE)

        chain = adapter.fetch_chain(_nifty(), date(2026, 10, 20))

        # Spot 25123.45 is nearer 25100 than 25000.
        assert chain.atm_strike().strike.rupees() == Decimal("25100")

    def test_timestamps_are_tz_aware(self) -> None:
        adapter, _ = _adapter(CHAIN_RESPONSE)

        chain = adapter.fetch_chain(_nifty(), date(2026, 10, 20))

        assert chain.received_at.tzinfo is not None
        assert chain.exchange_time.tzinfo is not None

    def test_an_empty_chain_raises_rather_than_storing_nothing(self) -> None:
        """A silent empty snapshot is worse than a failure: the gap gets trusted."""
        adapter, _ = _adapter({"status": "success", "data": {"last_price": 1, "oc": {}}})

        with pytest.raises(ValueError, match="no strikes"):
            adapter.fetch_chain(_nifty(), date(2026, 10, 20))


class TestRequestShape:
    def test_index_uses_the_index_segment(self) -> None:
        adapter, client = _adapter(CHAIN_RESPONSE)

        adapter.fetch_chain(_nifty(), date(2026, 10, 20))

        _, payload = client.calls[0]
        assert payload["UnderlyingSeg"] == "IDX_I"
        assert payload["UnderlyingScrip"] == 13
        assert payload["Expiry"] == "2026-10-20"

    def test_stock_uses_the_fno_segment(self) -> None:
        adapter, client = _adapter(CHAIN_RESPONSE)

        adapter.fetch_chain(_reliance(), date(2026, 10, 27))

        _, payload = client.calls[0]
        assert payload["UnderlyingSeg"] == "NSE_FNO"


class TestExpiryList:
    def test_expiries_are_parsed_and_sorted(self) -> None:
        adapter, _ = _adapter({"status": "success", "data": ["2026-11-24", "2026-10-20"]})

        expiries = adapter.available_expiries(_nifty())

        assert expiries == [date(2026, 10, 20), date(2026, 11, 24)]


class TestZeroIsTreatedAsNotReported:
    """Observed live: Dhan sends 0.0 rather than null for strikes it has not priced."""

    def test_zero_iv_becomes_none(self) -> None:
        body = {
            "status": "success",
            "data": {
                "last_price": 25000,
                "oc": {
                    "25000.000000": {
                        "ce": {
                            "last_price": 0,
                            "top_bid_price": 0,
                            "top_ask_price": 0,
                            "volume": 0,
                            "oi": 0,
                            "implied_volatility": 0.0,
                            "greeks": {"delta": 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0},
                        },
                        "pe": None,
                    }
                },
            },
        }
        adapter, _ = _adapter(body)

        call = adapter.fetch_chain(_nifty(), date(2026, 10, 20)).strikes[0].call

        assert call is not None
        assert call.implied_volatility is None
        assert call.delta is None
        assert call.gamma is None

    def test_a_real_reading_is_kept(self) -> None:
        adapter, _ = _adapter(CHAIN_RESPONSE)

        call = adapter.fetch_chain(_nifty(), date(2026, 10, 20)).strikes[0].call

        assert call is not None
        assert call.implied_volatility == 12.85
