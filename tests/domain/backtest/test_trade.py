"""Classifying why a losing trade lost.

The point of these modes is that each implicates a different parameter: a trade
stopped inside one session says something about the stop, and one that gapped
through it says the opposite - that a tighter stop would have made it worse.
A dashboard that only said "lost" would support neither conclusion.
"""

from __future__ import annotations

from decimal import Decimal

from trading_desk.domain.backtest.trade import ExitReason, FailureMode, SimulatedTrade
from trading_desk.domain.enums import Direction, TrendRegime, Verdict
from trading_desk.domain.gates import GateResult
from trading_desk.domain.values import Money, Price

REASON = GateResult("test", Verdict.PASS, "measured", "threshold")


def _trade(
    *,
    exit_rupees: str,
    reason: ExitReason,
    held: int,
    entry_rupees: str = "100",
    stop_rupees: str = "95",
    direction: Direction = Direction.BULLISH,
) -> SimulatedTrade:
    return SimulatedTrade(
        strategy="test",
        symbol="TESTCO",
        is_index=False,
        direction=direction,
        entry_date="2026-01-01",
        entry_price=Price.from_rupees(Decimal(entry_rupees)),
        exit_date="2026-01-05",
        exit_price=Price.from_rupees(Decimal(exit_rupees)),
        sessions_held=held,
        exit_reason=reason,
        stop_level=Price.from_rupees(Decimal(stop_rupees)),
        target_level=Price.from_rupees(Decimal("110")),
        atr_at_entry=Money.from_rupees(Decimal("5")),
        reasons=(REASON,),
        market_direction=TrendRegime.RANGING,
        against_market=False,
    )


class TestFailureModes:
    def test_a_profitable_trade_is_not_a_failure(self) -> None:
        trade = _trade(exit_rupees="110", reason=ExitReason.TARGET, held=4)
        assert trade.won
        assert trade.failure_mode is FailureMode.NOT_A_FAILURE

    def test_stopped_on_the_first_session(self) -> None:
        trade = _trade(exit_rupees="95", reason=ExitReason.STOP, held=1)
        assert trade.failure_mode is FailureMode.STOPPED_IMMEDIATELY

    def test_stopped_after_running_a_while(self) -> None:
        trade = _trade(exit_rupees="95", reason=ExitReason.STOP, held=6)
        assert trade.failure_mode is FailureMode.STOPPED_LATER

    def test_a_fill_worse_than_the_stop_is_a_gap(self) -> None:
        """The distinguishing case: exiting below the stop level means the
        session opened through it, which no stop placement prevents."""
        trade = _trade(exit_rupees="80", reason=ExitReason.STOP, held=2)
        assert trade.failure_mode is FailureMode.GAPPED_THROUGH_STOP

    def test_a_gap_is_detected_for_a_short_too(self) -> None:
        trade = _trade(
            exit_rupees="120",
            reason=ExitReason.STOP,
            held=2,
            stop_rupees="105",
            direction=Direction.BEARISH,
        )
        assert trade.failure_mode is FailureMode.GAPPED_THROUGH_STOP

    def test_time_stopped_having_barely_moved(self) -> None:
        """ATR is Rs 5, so a Rs 1 move is a fifth of an ATR - the signal picked
        a day that led nowhere, which is a selectivity problem not an exit one."""
        trade = _trade(exit_rupees="99", reason=ExitReason.TIME, held=20)
        assert trade.failure_mode is FailureMode.TIMED_OUT_FLAT

    def test_time_stopped_while_losing_steadily(self) -> None:
        trade = _trade(exit_rupees="96", reason=ExitReason.TIME, held=20)
        assert trade.failure_mode is FailureMode.TIMED_OUT_ADVERSE

    def test_every_mode_explains_itself(self) -> None:
        """Rule 9 in spirit: the dashboard shows this text, so none may be blank."""
        for mode in FailureMode:
            assert len(mode.explain()) > 20


class TestReturns:
    def test_costs_apply_to_a_winning_trade(self) -> None:
        trade = _trade(exit_rupees="110", reason=ExitReason.TARGET, held=4)
        assert trade.gross_return == 0.10
        assert trade.net_return < trade.gross_return

    def test_a_short_profits_when_price_falls(self) -> None:
        trade = _trade(
            exit_rupees="90", reason=ExitReason.TARGET, held=4, direction=Direction.BEARISH
        )
        assert trade.gross_return == 0.10
        assert trade.won

    def test_a_thin_win_becomes_a_loss_after_costs(self) -> None:
        """Median moves on the measured strategies are under 1%, so this is the
        common case rather than an edge case."""
        trade = _trade(exit_rupees="100.10", reason=ExitReason.TIME, held=20)
        assert trade.gross_return > 0
        assert not trade.won
