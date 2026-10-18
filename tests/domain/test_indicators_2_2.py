"""Indicators against hand-computed values, and the signal registry.

`docs/10` step 2.2 asks for "golden-file tests against hand-computed values".
Every expected number below is worked out in the comment beside it, so a
failure says which arithmetic changed rather than just that a number moved.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from trading_desk.domain.enums import Exchange, Segment, TrendRegime
from trading_desk.domain.indicators import (
    simple_moving_average,
    trend_regime,
    volume_ratio,
)
from trading_desk.domain.instruments import InstrumentRef
from trading_desk.domain.market import Bar
from trading_desk.domain.signals.registry import (
    REGISTRY,
    SignalStatus,
    UnbackedSignalError,
    UnknownSignalError,
    backtested_signals,
    is_registered,
    require_registered,
    require_usable,
)
from trading_desk.domain.timezones import IST
from trading_desk.domain.values import Money, Price

RELIANCE = InstrumentRef(
    symbol="RELIANCE", exchange=Exchange.NSE, segment=Segment.EQUITY, security_id="1", lot_size=1
)
START = datetime(2026, 1, 1, tzinfo=IST)


def _bar(day: int, close: str, volume: int = 1000) -> Bar:
    price = Price.from_rupees(Decimal(close))
    return Bar(
        instrument=RELIANCE,
        start=START + timedelta(days=day),
        open=price,
        high=price,
        low=price,
        close=price,
        volume=volume,
    )


def _series(closes: list[str], volumes: list[int] | None = None) -> list[Bar]:
    vols = volumes or [1000] * len(closes)
    return [_bar(day, close, vols[day]) for day, close in enumerate(closes)]


class TestMovingAverage:
    def test_a_hand_computed_mean(self) -> None:
        # (100 + 102 + 104 + 106 + 108) / 5 = 104
        bars = _series(["100", "102", "104", "106", "108"])

        assert simple_moving_average(bars, 5) == Money.from_rupees(Decimal("104"))

    def test_only_the_last_period_bars_count(self) -> None:
        # Last 3 of [10, 20, 30, 40, 50] -> (30 + 40 + 50) / 3 = 40
        bars = _series(["10", "20", "30", "40", "50"])

        assert simple_moving_average(bars, 3) == Money.from_rupees(Decimal("40"))

    def test_too_few_bars_is_none_not_a_partial_average(self) -> None:
        """A "20-day average" over six days is a different number, and a gate
        would act on it without knowing."""
        assert simple_moving_average(_series(["100"] * 6), 20) is None

    def test_rounding_is_half_up_at_the_paise(self) -> None:
        # (100.00 + 100.01) / 2 = 100.005 -> 10000.5 paise -> 10001 (half up)
        bars = _series(["100.00", "100.01"])

        assert simple_moving_average(bars, 2) == Money(paise=10001)

    def test_a_non_positive_period_raises(self) -> None:
        with pytest.raises(ValueError, match="period must be positive"):
            simple_moving_average(_series(["100"] * 5), 0)


class TestVolumeRatio:
    def test_a_hand_computed_ratio(self) -> None:
        # Baseline: four days at 1000 -> mean 1000. Latest 2500 -> 2.5x
        bars = _series(["100"] * 5, volumes=[1000, 1000, 1000, 1000, 2500])

        assert volume_ratio(bars, period=4) == 2.5

    def test_the_latest_bar_is_excluded_from_its_own_baseline(self) -> None:
        """The bug this guards: including today damps the spike it exists to
        find. Baseline 4x1000 = 1000 mean, so a 10,000 day is 10x. Including
        it would give 10000 / ((4*1000 + 10000) / 5) = 3.57x."""
        bars = _series(["100"] * 5, volumes=[1000, 1000, 1000, 1000, 10_000])

        assert volume_ratio(bars, period=4) == 10.0

    def test_a_quiet_day_is_below_one(self) -> None:
        bars = _series(["100"] * 5, volumes=[1000, 1000, 1000, 1000, 250])

        assert volume_ratio(bars, period=4) == 0.25

    def test_a_zero_baseline_is_none_not_infinity(self) -> None:
        """Unmeasurable, not infinitely unusual. A gate must be told which."""
        bars = _series(["100"] * 5, volumes=[0, 0, 0, 0, 5000])

        assert volume_ratio(bars, period=4) is None

    def test_too_few_bars_returns_none(self) -> None:
        # period + 1 bars are needed: 4 baseline plus the latest.
        assert volume_ratio(_series(["100"] * 4), period=4) is None
        assert volume_ratio(_series(["100"] * 5), period=4) is not None


class TestTrendRegime:
    def test_a_rising_stack_is_an_uptrend(self) -> None:
        bars = _series([str(100 + day) for day in range(60)])

        assert trend_regime(bars) is TrendRegime.UPTREND

    def test_a_falling_stack_is_a_downtrend(self) -> None:
        bars = _series([str(200 - day) for day in range(60)])

        assert trend_regime(bars) is TrendRegime.DOWNTREND

    def test_a_flat_market_is_ranging_not_a_trend(self) -> None:
        """The common answer, and a classifier that rarely says it is
        overfitting to noise."""
        bars = _series(["100", "101", "99", "100", "101", "99"] * 10)

        assert trend_regime(bars) is TrendRegime.RANGING

    def test_too_little_history_is_distinct_from_ranging(self) -> None:
        """ "We cannot say" and "no trend" are different answers. Collapsing
        them makes a new listing look like a quiet one."""
        assert trend_regime(_series(["100"] * 30)) is TrendRegime.INSUFFICIENT_HISTORY


class TestSignalRegistry:
    def test_an_invented_signal_is_refused_by_name(self) -> None:
        """What an LLM actually does: names a plausible signal that does not
        exist, with a confident paragraph attached."""
        with pytest.raises(UnknownSignalError, match="9_day_momentum_crossover"):
            require_registered("9_day_momentum_crossover")

    def test_the_error_lists_what_is_available(self) -> None:
        with pytest.raises(UnknownSignalError, match="atr_14"):
            require_registered("not_a_signal")

    def test_a_registered_signal_resolves(self) -> None:
        assert require_registered("atr_14").computed_by.endswith("average_true_range")
        assert is_registered("iv_rank")

    def test_nothing_is_usable_until_the_dry_run_has_evidence(self) -> None:
        """FR-5.10: only pre-backtested signals may be referenced. Step 2.8 has
        not run, so every call raises. This test flips when 2.8 lands."""
        with pytest.raises(UnbackedSignalError, match="IMPLEMENTED"):
            require_usable("atr_14")

        assert backtested_signals() == []

    def test_no_signal_claims_evidence_it_does_not_have(self) -> None:
        """Guards against someone flipping a status flag ahead of the backtest."""
        claiming = [
            name for name, item in REGISTRY.items() if item.status is SignalStatus.BACKTESTED
        ]
        assert claiming == [], f"claims a backtest before step 2.8 ran: {claiming}"

    def test_every_entry_states_its_evidence_and_where_it_is_computed(self) -> None:
        incomplete = [
            name
            for name, item in REGISTRY.items()
            if not item.evidence.strip() or not item.computed_by.strip()
        ]
        assert incomplete == []
