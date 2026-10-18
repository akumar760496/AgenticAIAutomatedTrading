"""True range, ATR and the 20-day range.

The ATR fixture below is computed by hand in the test body. docs/12 asks for that
once, because every later gate reads ATR and a wrong one is invisible: it produces
plausible signals at slightly the wrong times.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from trading_desk.domain.enums import Exchange, Segment
from trading_desk.domain.indicators import (
    average_true_range,
    percent_change,
    price_range,
    true_range,
    true_ranges,
)
from trading_desk.domain.instruments import InstrumentRef
from trading_desk.domain.market import Bar
from trading_desk.domain.values import Money, Price

INSTRUMENT = InstrumentRef(
    symbol="TESTCO",
    exchange=Exchange.NSE,
    segment=Segment.EQUITY,
    security_id="1",
    lot_size=1,
)


def _bar(day: int, high: str, low: str, close: str, open_: str | None = None) -> Bar:
    return Bar(
        instrument=INSTRUMENT,
        start=datetime(2026, 1, day, tzinfo=UTC),
        open=Price.from_rupees(Decimal(open_ or close)),
        high=Price.from_rupees(Decimal(high)),
        low=Price.from_rupees(Decimal(low)),
        close=Price.from_rupees(Decimal(close)),
        volume=100,
    )


def _rupees(amount: str) -> Money:
    return Money.from_rupees(amount)


class TestTrueRange:
    def test_first_bar_uses_only_its_own_range(self) -> None:
        """No previous close exists. Substituting the open makes every later
        smoothed value wrong, because the error propagates through the recursion."""
        assert true_range(_bar(1, "110", "100", "105"), None) == _rupees("10")

    def test_a_gap_up_beats_the_intraday_range(self) -> None:
        """Yesterday closed at 100, today trades 115-112. The real range is 15."""
        bar = _bar(2, "115", "112", "114")
        assert true_range(bar, Price.from_rupees(Decimal("100"))) == _rupees("15")

    def test_a_gap_down_beats_the_intraday_range(self) -> None:
        bar = _bar(2, "88", "85", "86")
        assert true_range(bar, Price.from_rupees(Decimal("100"))) == _rupees("15")

    def test_an_inside_day_uses_the_intraday_range(self) -> None:
        bar = _bar(2, "104", "98", "101")
        assert true_range(bar, Price.from_rupees(Decimal("100"))) == _rupees("6")

    def test_series_carries_the_previous_close_forward(self) -> None:
        bars = [_bar(1, "110", "100", "105"), _bar(2, "112", "104", "110")]

        # TR2 = max(112-104=8, |112-105|=7, |104-105|=1) = 8
        assert true_ranges(bars) == [_rupees("10"), _rupees("8")]


class TestAverageTrueRange:
    def test_matches_a_hand_computed_value(self) -> None:
        """Worked by hand, in paise, so the arithmetic is auditable:

        TRs:   1000, 800, 700, 1000
        first: (1000 + 800 + 700) / 3        = 833.333...
        then:  (833.333... * 2 + 1000) / 3   = 888.888...
        round half up                        = 889 paise = Rs 8.89
        """
        bars = [
            _bar(1, "110", "100", "105"),
            _bar(2, "112", "104", "110"),
            _bar(3, "115", "108", "112"),
            _bar(4, "120", "110", "118"),
        ]

        assert true_ranges(bars) == [
            _rupees("10"),
            _rupees("8"),
            _rupees("7"),
            _rupees("10"),
        ]
        assert average_true_range(bars, period=3) == Money(889)

    def test_uses_wilder_smoothing_not_a_simple_mean(self) -> None:
        """A simple mean of the last 3 TRs would be (800+700+1000)/3 = 833."""
        bars = [
            _bar(1, "110", "100", "105"),
            _bar(2, "112", "104", "110"),
            _bar(3, "115", "108", "112"),
            _bar(4, "120", "110", "118"),
        ]

        assert average_true_range(bars, period=3) != Money(833)

    def test_rounds_once_rather_than_at_every_step(self) -> None:
        """Truncating each step biases ATR downward by a few paise, always in the
        same direction, which makes a 1.5x ATR gate fire slightly too often."""
        bars = [_bar(day, "110", "100", "105") for day in range(1, 29)]

        # Every TR is exactly 10 rupees, so any correct method returns 10.
        assert average_true_range(bars, period=14) == _rupees("10")

    def test_returns_none_without_enough_bars(self) -> None:
        bars = [_bar(1, "110", "100", "105"), _bar(2, "112", "104", "110")]

        assert average_true_range(bars, period=14) is None

    def test_exactly_period_bars_is_enough(self) -> None:
        bars = [_bar(day, "110", "100", "105") for day in range(1, 4)]

        assert average_true_range(bars, period=3) == _rupees("10")

    def test_rejects_a_non_positive_period(self) -> None:
        with pytest.raises(ValueError, match="period must be positive"):
            average_true_range([_bar(1, "110", "100", "105")], period=0)


class TestPriceRange:
    def test_spans_highest_high_to_lowest_low(self) -> None:
        bars = [
            _bar(1, "110", "100", "105"),
            _bar(2, "120", "108", "118"),
            _bar(3, "115", "95", "100"),
        ]

        assert price_range(bars) == _rupees("25")  # 120 - 95

    def test_empty_input_has_no_range(self) -> None:
        assert price_range([]) is None

    def test_the_caller_slices_the_window(self) -> None:
        bars = [_bar(1, "200", "10", "100")] + [
            _bar(day, "110", "100", "105") for day in range(2, 25)
        ]

        assert price_range(bars[-20:]) == _rupees("10")  # the outlier is outside


class TestPercentChange:
    def test_a_rise(self) -> None:
        assert percent_change(
            Price.from_rupees(Decimal("100")), Price.from_rupees(Decimal("110"))
        ) == pytest.approx(10.0)

    def test_a_fall_is_negative(self) -> None:
        assert percent_change(
            Price.from_rupees(Decimal("100")), Price.from_rupees(Decimal("50"))
        ) == pytest.approx(-50.0)

    def test_a_zero_base_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="zero price"):
            percent_change(Price(0), Price.from_rupees(Decimal("10")))
