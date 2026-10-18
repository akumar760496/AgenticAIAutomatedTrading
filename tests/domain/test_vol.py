"""Volatility maths. The refusals matter as much as the numbers."""

from __future__ import annotations

from decimal import Decimal

import pytest

from trading_desk.domain.vol import (
    MAX_PLAUSIBLE_IV,
    MIN_SAMPLES,
    IvSource,
    combine_atm_iv,
    percentile_of,
    rank_within,
    realised_volatility,
)


class TestPercentile:
    def test_the_highest_value_is_the_hundredth_percentile(self) -> None:
        assert percentile_of(50.0, [10.0, 20.0, 30.0, 40.0]) == 100.0

    def test_the_lowest_value_is_the_zeroth(self) -> None:
        assert percentile_of(5.0, [10.0, 20.0, 30.0, 40.0]) == 0.0

    def test_a_typical_day_in_a_flat_series_is_fifty_not_zero_or_a_hundred(self) -> None:
        """Ties count as half. With a strict < the answer is 0 and with <= it is
        100, and both say something false about a day that is exactly average."""
        assert percentile_of(20.0, [20.0] * 10) == 50.0

    def test_an_empty_history_raises_rather_than_returning_zero(self) -> None:
        with pytest.raises(ValueError, match="at least one"):
            percentile_of(20.0, [])


class TestRankRefusal:
    def test_below_the_minimum_sample_count_no_number_is_returned(self) -> None:
        """docs/12 asks for this explicitly: a rank from 11 days is a lie with a
        decimal point, because each observation moves it by nine points."""
        rank = rank_within(20.0, [1.0] * 11, IvSource.ACTUAL)

        assert rank.value is None
        assert not rank.is_available
        assert "insufficient history" in rank.reason
        assert rank.sample_count == 11

    def test_exactly_the_minimum_is_enough(self) -> None:
        rank = rank_within(20.0, [1.0] * MIN_SAMPLES, IvSource.ACTUAL)

        assert rank.is_available
        assert rank.sample_count == MIN_SAMPLES

    def test_one_below_the_minimum_is_not(self) -> None:
        """The boundary, from the other side - so an off-by-one cannot hide."""
        assert not rank_within(20.0, [1.0] * (MIN_SAMPLES - 1), IvSource.ACTUAL).is_available

    def test_the_window_caps_the_history_used(self) -> None:
        rank = rank_within(99.0, [10.0] * 300, IvSource.ACTUAL, window=50)

        assert rank.sample_count == 50  # not 300
        assert rank.value == 100.0

    def test_a_new_high_reads_as_a_hundred_not_ninety_nine(self) -> None:
        """History is prior days only. Including today in its own history caps
        the result below 100, which misreports a reading never seen before."""
        assert rank_within(99.0, [10.0] * 40, IvSource.ACTUAL).value == 100.0

    def test_the_source_is_carried_through_both_ways(self) -> None:
        assert rank_within(20.0, [1.0] * 40, IvSource.PROXY).source is IvSource.PROXY
        assert rank_within(20.0, [1.0] * 11, IvSource.PROXY).source is IvSource.PROXY


class TestAtmIv:
    def test_call_and_put_are_averaged(self) -> None:
        assert combine_atm_iv(20.0, 22.0) == 21.0

    def test_one_usable_side_is_still_a_measurement(self) -> None:
        assert combine_atm_iv(20.0, None) == 20.0

    def test_an_implausible_value_is_dropped_not_clamped(self) -> None:
        """Measured live in step 1.1b: a zero-volume deep-ITM strike carried a
        stale last implying 419% vol. Clamping keeps a fabricated number in the
        series; dropping leaves an honest gap."""
        assert combine_atm_iv(419.45, 22.0) == 22.0
        assert combine_atm_iv(419.45, None) is None
        assert combine_atm_iv(MAX_PLAUSIBLE_IV + 0.01, None) is None

    def test_no_usable_side_is_none(self) -> None:
        assert combine_atm_iv(None, None) is None


class TestRealisedVolatility:
    def test_a_flat_series_has_zero_volatility(self) -> None:
        assert realised_volatility([Decimal("100")] * 10) == 0.0

    def test_a_more_volatile_series_scores_higher(self) -> None:
        calm = [Decimal(str(100 + (i % 2))) for i in range(30)]
        wild = [Decimal(str(100 + (i % 2) * 10)) for i in range(30)]

        assert realised_volatility(wild) > realised_volatility(calm)  # type: ignore[operator]

    def test_too_few_prices_returns_none_rather_than_zero(self) -> None:
        """Zero volatility and unknown volatility are different answers."""
        assert realised_volatility([Decimal("100")]) is None
        assert realised_volatility([]) is None

    def test_a_non_positive_price_is_refused(self) -> None:
        """A log return needs a positive price. Zero is bad data, not a 0% day."""
        assert realised_volatility([Decimal("100"), Decimal("0"), Decimal("100")]) is None
