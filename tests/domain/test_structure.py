"""The structure builder, over fixture chains.

The test that matters most is the last class: **no code path emits a naked
short** (FR-5.9). A thin chain with no wing available must raise, not return a
single leg.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from trading_desk.domain.enums import Direction, Exchange, OptionRight, Segment
from trading_desk.domain.instruments import InstrumentRef
from trading_desk.domain.market import OptionChainSnapshot, OptionQuote, OptionStrike
from trading_desk.domain.options.structure import (
    StructureError,
    VerticalSpread,
    build_credit_vertical,
    build_debit_vertical,
    max_loss_in_rupees,
    select_by_delta,
)
from trading_desk.domain.setups.thresholds import DeltaBand
from trading_desk.domain.values import Money, Price

NIFTY = InstrumentRef(
    symbol="NIFTY", exchange=Exchange.NSE, segment=Segment.FNO, security_id="26000", lot_size=75
)
NOW = datetime(2026, 10, 19, 10, 0, tzinfo=UTC)

LONG_BAND = DeltaBand(0.50, 0.55)
SHORT_BAND = DeltaBand(0.25, 0.30)


def _quote(right: OptionRight, mid: str, delta: float, spread: str = "1") -> OptionQuote:
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


def _chain(rows: list[tuple[str, str, float, str, float]]) -> OptionChainSnapshot:
    """rows: (strike, call mid, call delta, put mid, put delta)."""
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
        expiry_date="2026-11-26",
        strikes=strikes,
        spot=Price.from_rupees(Decimal("24000")),
        exchange_time=NOW,
        received_at=NOW,
    )


def _standard_chain() -> OptionChainSnapshot:
    """A realistic seven-strike chain: deltas fall as calls go out of the money
    and rise as puts do, and both wings are priced so a protective leg exists."""
    return _chain(
        [
            ("23400", "520", 0.80, "40", -0.20),
            ("23600", "420", 0.72, "60", -0.28),
            ("23800", "330", 0.62, "95", -0.38),
            ("24000", "240", 0.52, "150", -0.48),
            ("24200", "165", 0.46, "230", -0.54),
            ("24400", "105", 0.30, "340", -0.70),
            ("24600", "62", 0.22, "480", -0.78),
        ]
    )


class TestStrikeSelection:
    def test_a_strike_inside_the_band_is_chosen(self) -> None:
        strike = select_by_delta(_standard_chain(), OptionRight.CALL, LONG_BAND)

        assert strike is not None
        assert strike.strike == Price.from_rupees(Decimal("24000"))

    def test_the_closest_to_the_middle_of_the_band_wins(self) -> None:
        """Not the first match: chains are unevenly spaced, and first-match
        biases every structure toward one edge of the band."""
        chain = _chain(
            [
                ("24000", "220", 0.505, "150", -0.50),
                ("24100", "180", 0.525, "190", -0.48),  # closest to 0.525 mid
                ("24200", "140", 0.548, "260", -0.45),
            ]
        )
        strike = select_by_delta(chain, OptionRight.CALL, LONG_BAND)

        assert strike is not None
        assert strike.strike == Price.from_rupees(Decimal("24100"))

    def test_no_strike_in_the_band_returns_none(self) -> None:
        chain = _chain([("24600", "45", 0.05, "560", -0.95)])
        assert select_by_delta(chain, OptionRight.CALL, LONG_BAND) is None

    def test_an_unpriceable_strike_is_skipped(self) -> None:
        """A leg quoted far wider than mid cannot be filled near the price the
        structure's economics assume."""
        wide = OptionStrike(
            strike=Price.from_rupees(Decimal("24000")),
            call=_quote(OptionRight.CALL, "220", 0.52, spread="100"),
            put=None,
        )
        chain = OptionChainSnapshot(
            underlying=NIFTY,
            expiry_date="2026-11-26",
            strikes=(wide,),
            spot=Price.from_rupees(Decimal("24000")),
            exchange_time=NOW,
            received_at=NOW,
        )
        assert select_by_delta(chain, OptionRight.CALL, LONG_BAND) is None


class TestDebitVertical:
    def test_the_economics_are_hand_checkable(self) -> None:
        # Long 24000 call at 240 (delta 0.52), short 24400 call at 105
        # (delta 0.30) -> net debit 135. Width 400, so max gain = 400 - 135
        # = 265. Breakeven = 24000 + 135.
        spread = build_debit_vertical(_standard_chain(), Direction.BULLISH, LONG_BAND, SHORT_BAND)

        assert spread.net_debit == Money.from_rupees(Decimal("135"))
        assert spread.max_loss == Money.from_rupees(Decimal("135"))
        assert spread.max_gain == Money.from_rupees(Decimal("265"))
        assert spread.width == Money.from_rupees(Decimal("400"))
        assert spread.breakeven == Price.from_rupees(Decimal("24135"))

    def test_max_loss_plus_max_gain_equals_the_width(self) -> None:
        """The defining property of a vertical. If it ever fails, the payoff is
        not what the briefing claims."""
        spread = build_debit_vertical(_standard_chain(), Direction.BULLISH, LONG_BAND, SHORT_BAND)

        assert spread.max_loss.paise + spread.max_gain.paise == spread.width.paise

    def test_a_bearish_debit_uses_puts_and_breaks_even_below(self) -> None:
        spread = build_debit_vertical(_standard_chain(), Direction.BEARISH, LONG_BAND, SHORT_BAND)

        assert spread.long_leg.right is OptionRight.PUT
        assert spread.breakeven.paise < spread.long_leg.strike.paise

    def test_a_thin_chain_is_rejected_not_approximated(self) -> None:
        chain = _chain([("24000", "220", 0.52, "150", -0.48)])

        with pytest.raises(StructureError, match="no strikes in the delta bands"):
            build_debit_vertical(chain, Direction.BULLISH, LONG_BAND, SHORT_BAND)

    def test_a_chain_too_coarse_to_separate_the_legs_is_rejected(self) -> None:
        """One strike satisfying both bands would produce a zero-width spread."""
        chain = _chain([("24000", "220", 0.40, "150", -0.40)])
        band = DeltaBand(0.30, 0.50)

        with pytest.raises(StructureError, match="same strike"):
            build_debit_vertical(chain, Direction.BULLISH, band, band)

    def test_a_neutral_direction_cannot_build_a_vertical(self) -> None:
        with pytest.raises(StructureError, match="needs a direction"):
            build_debit_vertical(_standard_chain(), Direction.NEUTRAL, LONG_BAND, SHORT_BAND)


class TestCreditVerticalAlwaysHasAWing:
    def test_a_credit_spread_has_exactly_two_legs_one_of_them_long(self) -> None:
        spread = build_credit_vertical(
            _standard_chain(),
            Direction.BULLISH,
            DeltaBand(0.30, 0.40),
            wing_offset=Money.from_rupees(Decimal("200")),
        )

        assert spread.is_credit
        assert spread.long_leg.right is OptionRight.PUT
        assert spread.short_leg.right is OptionRight.PUT
        assert spread.long_leg.strike.paise != spread.short_leg.strike.paise

    def test_max_loss_is_width_minus_credit(self) -> None:
        spread = build_credit_vertical(
            _standard_chain(),
            Direction.BULLISH,
            DeltaBand(0.30, 0.40),
            wing_offset=Money.from_rupees(Decimal("200")),
        )

        assert spread.max_loss.paise + spread.max_gain.paise == spread.width.paise

    def test_no_wing_available_raises_rather_than_returning_a_naked_short(self) -> None:
        """FR-5.9 as a control-flow decision. The chain has one priced put, so
        there is nothing to buy as protection."""
        only_one = OptionStrike(
            strike=Price.from_rupees(Decimal("23800")),
            call=None,
            put=_quote(OptionRight.PUT, "90", -0.35),
        )
        chain = OptionChainSnapshot(
            underlying=NIFTY,
            expiry_date="2026-11-26",
            strikes=(only_one,),
            spot=Price.from_rupees(Decimal("24000")),
            exchange_time=NOW,
            received_at=NOW,
        )

        with pytest.raises(StructureError, match="naked short"):
            build_credit_vertical(
                chain,
                Direction.BULLISH,
                DeltaBand(0.30, 0.40),
                wing_offset=Money.from_rupees(Decimal("200")),
            )

    def test_no_builder_can_return_a_single_leg_structure(self) -> None:
        """Introspection rather than trust: every builder's return type is a
        two-leg spread, so a one-legged result is not expressible."""
        fields = VerticalSpread.__dataclass_fields__
        assert "long_leg" in fields
        assert "short_leg" in fields
        # Neither is optional, so a spread without a long leg cannot be built.
        assert "None" not in str(fields["long_leg"].type)


def test_max_loss_scales_by_lot_size_without_the_structure_knowing_a_quantity() -> None:
    """Sizing is the risk gate's job. This only scales per-lot economics for
    display, and takes the lot size as an argument rather than holding one."""
    spread = build_debit_vertical(_standard_chain(), Direction.BULLISH, LONG_BAND, SHORT_BAND)

    assert max_loss_in_rupees(spread, NIFTY.lot_size) == Decimal("135") * 75
    assert not hasattr(spread, "quantity")
