"""The exit policy's arithmetic, and what it is allowed to claim.

`breakeven_win_rate` exists because a win rate means nothing on its own: 37%
is poor at 1.5:1 and excellent at 5:1. The tests below pin the number, and pin
the distinction between this theoretical figure and the realised one - a
distinction the first dashboard run got wrong, printing "beats breakeven" for
a strategy whose expectancy was negative.
"""

from __future__ import annotations

import pytest

from trading_desk.domain.backtest.strategy import ExitPolicy


class TestBreakeven:
    def test_a_one_to_one_policy_needs_half(self) -> None:
        policy = ExitPolicy(stop_in_atr=1.0, target_in_atr=1.0, max_sessions_held=10)
        assert policy.breakeven_win_rate == pytest.approx(0.50)

    def test_the_stock_policy_needs_forty_percent(self) -> None:
        """1.0 stop against a 1.5 target. The figure ISS-039 is measured against."""
        policy = ExitPolicy(stop_in_atr=1.0, target_in_atr=1.5, max_sessions_held=20)
        assert policy.breakeven_win_rate == pytest.approx(0.40)

    def test_a_wider_target_lowers_the_bar(self) -> None:
        policy = ExitPolicy(stop_in_atr=1.0, target_in_atr=4.0, max_sessions_held=10)
        assert policy.breakeven_win_rate == pytest.approx(0.20)

    def test_it_is_theoretical_and_therefore_optimistic(self) -> None:
        """It assumes every win is the full target and every loss the full stop.
        Gaps overshoot the stop and time stops exit part-way, so the realised
        bar is always higher. Pinned so nobody reads it as the real one."""
        policy = ExitPolicy(stop_in_atr=1.0, target_in_atr=2.0, max_sessions_held=10)
        assert policy.breakeven_win_rate == pytest.approx(1 / 3)


class TestWhatAPolicyRefuses:
    @pytest.mark.parametrize(("stop", "target"), [(0.0, 1.5), (1.0, 0.0), (-1.0, 1.5)])
    def test_a_non_positive_multiple_is_refused(self, stop: float, target: float) -> None:
        with pytest.raises(ValueError, match="positive ATR"):
            ExitPolicy(stop_in_atr=stop, target_in_atr=target, max_sessions_held=10)

    def test_a_trade_must_be_allowed_to_last_a_session(self) -> None:
        with pytest.raises(ValueError, match="max_sessions_held"):
            ExitPolicy(stop_in_atr=1.0, target_in_atr=1.5, max_sessions_held=0)
