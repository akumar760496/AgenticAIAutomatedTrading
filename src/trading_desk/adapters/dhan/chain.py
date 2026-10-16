"""Option chain adapter: Dhan's JSON in, OptionChainSnapshot out.

This is the only file that knows Dhan's field names. When they rename
`top_bid_price`, exactly this file changes and nothing above it notices.

Mapping is written as an explicit loop rather than a comprehension because the
day a field moves, the line that breaks should be obvious.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from trading_desk.adapters.dhan.client import DhanClient
from trading_desk.domain.enums import OptionRight
from trading_desk.domain.instruments import InstrumentRef
from trading_desk.domain.market import (
    OptionChainSnapshot,
    OptionQuote,
    OptionStrike,
)
from trading_desk.domain.values import Price
from trading_desk.ports.clock import Clock

CHAIN_PATH = "/optionchain"
EXPIRY_LIST_PATH = "/optionchain/expirylist"

# Dhan's segment codes. Indices and equity derivatives are requested differently.
SEGMENT_INDEX = "IDX_I"
SEGMENT_NSE_FNO = "NSE_FNO"

# Index underlyings use IDX_I; everything else is a stock in NSE_FNO.
INDEX_SYMBOLS = frozenset({"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX", "BANKEX"})


def _price_from(raw: Any) -> Price:
    """Dhan sends prices as JSON numbers. Convert via str so no float ever rounds."""
    return Price.from_rupees(Decimal(str(raw or 0)))


def _optional_float(raw: Any) -> float | None:
    """Greeks and IV where Dhan reports them, None where it does not.

    Dhan sends 0.0 rather than null for strikes it has not priced - observed live
    on 2026-10-16, where 740 of 740 BANKNIFTY rows had a non-null IV and many were
    exactly 0.0 on strikes with no last price, no volume and no open interest.

    An option with an implied volatility of exactly zero does not exist, and a
    greek of exactly 0.0 is a placeholder rather than a measurement. Storing those
    zeros would drag the IV-rank percentile down for every strike that was simply
    not quoted - permanently, because this series cannot be rebuilt.

    So exact zero is treated as "not reported". The cost is losing a genuine
    0.0 reading that never occurs in practice; the benefit is an IV history that
    means what it says.
    """
    if raw is None or raw == "":
        return None
    value = float(raw)
    if value == 0.0:
        return None
    return value


class DhanOptionChainAdapter:
    """Implements OptionChainPort against DhanHQ v2."""

    def __init__(self, client: DhanClient, clock: Clock) -> None:
        self._client = client
        self._clock = clock

    def available_expiries(self, underlying: InstrumentRef) -> list[date]:
        """Expiries the exchange currently lists, soonest first.

        Read rather than computed. NIFTY is weekly on Tuesday and BANKNIFTY is
        monthly only, and both rules changed within the last two years.
        """
        body = self._client.post(
            EXPIRY_LIST_PATH,
            {
                "UnderlyingScrip": int(underlying.security_id),
                "UnderlyingSeg": self._segment_for(underlying),
            },
        )
        raw_dates = body.get("data", [])
        return sorted(date.fromisoformat(value) for value in raw_dates)

    def fetch_chain(self, underlying: InstrumentRef, expiry: date) -> OptionChainSnapshot:
        """The full chain for one underlying and expiry, as it stands right now."""
        received_at = self._clock.utc_now()
        body = self._client.post(
            CHAIN_PATH,
            {
                "UnderlyingScrip": int(underlying.security_id),
                "UnderlyingSeg": self._segment_for(underlying),
                "Expiry": expiry.isoformat(),
            },
        )

        data = body.get("data", {})
        strikes = self._map_strikes(data.get("oc", {}))
        if not strikes:
            raise ValueError(f"{underlying.symbol} {expiry}: Dhan returned a chain with no strikes")

        return OptionChainSnapshot(
            underlying=underlying,
            expiry_date=expiry.isoformat(),
            strikes=tuple(strikes),
            spot=_price_from(data.get("last_price")),
            # Dhan's chain response carries no exchange timestamp, so receipt time
            # stands in for both. Recorded here so nobody later mistakes the
            # equality for a bug.
            exchange_time=received_at,
            received_at=received_at,
        )

    def _map_strikes(self, raw_chain: dict[str, Any]) -> list[OptionStrike]:
        strikes = []
        for raw_strike, sides in sorted(raw_chain.items(), key=lambda item: float(item[0])):
            strikes.append(
                OptionStrike(
                    strike=_price_from(raw_strike),
                    call=self._map_quote(sides.get("ce"), OptionRight.CALL),
                    put=self._map_quote(sides.get("pe"), OptionRight.PUT),
                )
            )
        return strikes

    def _map_quote(self, raw: dict[str, Any] | None, right: OptionRight) -> OptionQuote | None:
        """One side of one strike. Returns None when the exchange lists nothing."""
        if not raw:
            return None

        greeks = raw.get("greeks") or {}
        return OptionQuote(
            right=right,
            last=_price_from(raw.get("last_price")),
            bid=_price_from(raw.get("top_bid_price")),
            ask=_price_from(raw.get("top_ask_price")),
            volume=int(raw.get("volume") or 0),
            open_interest=int(raw.get("oi") or 0),
            implied_volatility=_optional_float(raw.get("implied_volatility")),
            delta=_optional_float(greeks.get("delta")),
            gamma=_optional_float(greeks.get("gamma")),
            theta=_optional_float(greeks.get("theta")),
            vega=_optional_float(greeks.get("vega")),
        )

    @staticmethod
    def _segment_for(underlying: InstrumentRef) -> str:
        if underlying.symbol in INDEX_SYMBOLS:
            return SEGMENT_INDEX
        return SEGMENT_NSE_FNO
