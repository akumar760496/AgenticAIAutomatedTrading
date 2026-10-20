"""The three rules that stop a backtest flattering itself.

Each of these is a way a simulation can quietly overstate a strategy, and each
is tested by inducing the exact situation rather than asserting the code path.
`docs/12` calls this the difference between a check and a proof.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from tests.domain.backtest.fake_strategy import FakeStrategy
from trading_desk.domain.backtest.engine import MarketContext, run_strategy, simulate_one
from trading_desk.domain.backtest.strategy import ExitPolicy, Signal
from trading_desk.domain.backtest.trade import ExitReason
from trading_desk.domain.enums import Direction, Exchange, Segment, TrendRegime, Verdict
from trading_desk.domain.gates import GateResult
from trading_desk.domain.instruments import InstrumentRef
from trading_desk.domain.market import Bar
from trading_desk.domain.values import Price

INSTRUMENT = InstrumentRef(
    symbol="TESTCO", exchange=Exchange.NSE, segment=Segment.EQUITY, security_id="1", lot_size=1
)
REASON = GateResult("test", Verdict.PASS, "measured", "threshold")
FLAT_MARKET = MarketContext(regime_by_date={})


def _rupees(amount: str) -> Price:
    return Price.from_rupees(Decimal(amount))


def bar(day: int, *, open_: str, high: str, low: str, close: str) -> Bar:
    return Bar(
        instrument=INSTRUMENT,
        start=datetime(2026, 1, day, tzinfo=UTC),
        open=_rupees(open_),
        high=_rupees(high),
        low=_rupees(low),
        close=_rupees(close),
        volume=1_000,
    )


def _steady(count: int, price: str = "100") -> list[Bar]:
    """A flat series long enough for ATR(14) to exist."""
    return [bar(day, open_=price, high="101", low="99", close=price) for day in range(1, count + 1)]


class TestEntryIsNotTheSignalBar:
    def test_entry_uses_the_open_after_the_signal(self) -> None:
        """The signal IS the signal bar's close. Trading that close is lookahead."""
        bars = _steady(20)
        bars.append(bar(21, open_="110", high="111", low="109", close="110"))
        bars.extend(
            bar(day, open_="110", high="111", low="109", close="110") for day in range(22, 30)
        )
        exits = ExitPolicy(stop_in_atr=1.0, target_in_atr=1.5, max_sessions_held=5)

        trade = simulate_one(
            bars,
            19,
            signal=Signal(Direction.BULLISH, (REASON,)),
            strategy=FakeStrategy(19, Direction.BULLISH, exits),
            market=FLAT_MARKET,
        )

        assert trade is not None
        assert trade.entry_price.rupees() == Decimal("110")
        assert trade.entry_date == "2026-01-21"


class TestATieGoesToTheStop:
    def test_a_bar_holding_both_levels_is_scored_a_stop(self) -> None:
        """A daily bar cannot say which came first. Assuming the target is the
        single most flattering assumption available, so the stop wins."""
        bars = _steady(20)
        bars.append(bar(21, open_="100", high="101", low="99", close="100"))
        # A bar wide enough to contain both the stop and the target.
        bars.append(bar(22, open_="100", high="140", low="60", close="100"))
        bars.extend(
            bar(day, open_="100", high="101", low="99", close="100") for day in range(23, 28)
        )
        exits = ExitPolicy(stop_in_atr=1.0, target_in_atr=1.5, max_sessions_held=5)

        trade = simulate_one(
            bars,
            19,
            signal=Signal(Direction.BULLISH, (REASON,)),
            strategy=FakeStrategy(19, Direction.BULLISH, exits),
            market=FLAT_MARKET,
        )

        assert trade is not None
        assert trade.exit_reason is ExitReason.STOP
        assert not trade.won


class TestAGapFillsAtTheOpen:
    def test_a_gap_through_the_stop_does_not_fill_at_the_stop(self) -> None:
        """Honouring the stop level on a gap invents liquidity that was not
        there, and turns the worst real outcome into a controlled one."""
        bars = _steady(20)
        bars.append(bar(21, open_="100", high="101", low="99", close="100"))
        bars.append(bar(22, open_="70", high="72", low="68", close="70"))
        bars.extend(bar(day, open_="70", high="71", low="69", close="70") for day in range(23, 28))
        exits = ExitPolicy(stop_in_atr=1.0, target_in_atr=1.5, max_sessions_held=5)

        trade = simulate_one(
            bars,
            19,
            signal=Signal(Direction.BULLISH, (REASON,)),
            strategy=FakeStrategy(19, Direction.BULLISH, exits),
            market=FLAT_MARKET,
        )

        assert trade is not None
        assert trade.exit_price.rupees() == Decimal("70")
        assert trade.exit_price.paise < trade.stop_level.paise


class TestAgainstTheMarket:
    def test_a_long_in_a_downtrend_is_flagged_against(self) -> None:
        bars = _steady(20)
        bars.extend(
            bar(day, open_="100", high="101", low="99", close="100") for day in range(21, 28)
        )
        market = MarketContext({"2026-01-21": TrendRegime.DOWNTREND})
        exits = ExitPolicy(stop_in_atr=1.0, target_in_atr=1.5, max_sessions_held=5)

        trade = simulate_one(
            bars,
            19,
            signal=Signal(Direction.BULLISH, (REASON,)),
            strategy=FakeStrategy(19, Direction.BULLISH, exits),
            market=market,
        )

        assert trade is not None
        assert trade.against_market
        assert trade.market_direction is TrendRegime.DOWNTREND

    def test_a_ranging_market_is_not_against_anything(self) -> None:
        """The column answers "did this fight the trend", not "was there one"."""
        bars = _steady(20)
        bars.extend(
            bar(day, open_="100", high="101", low="99", close="100") for day in range(21, 28)
        )
        exits = ExitPolicy(stop_in_atr=1.0, target_in_atr=1.5, max_sessions_held=5)

        trade = simulate_one(
            bars,
            19,
            signal=Signal(Direction.BULLISH, (REASON,)),
            strategy=FakeStrategy(19, Direction.BULLISH, exits),
            market=FLAT_MARKET,
        )

        assert trade is not None
        assert not trade.against_market
        assert trade.market_direction is TrendRegime.RANGING


class TestTheNewsColumnStaysHonest:
    def test_news_is_unavailable_rather_than_no(self) -> None:
        """The filings archive starts 2026-10-14. For any earlier trade there is
        nothing to check, and 'no' would read as evidence (rule 6)."""
        bars = _steady(20)
        bars.extend(
            bar(day, open_="100", high="101", low="99", close="100") for day in range(21, 28)
        )
        exits = ExitPolicy(stop_in_atr=1.0, target_in_atr=1.5, max_sessions_held=5)

        trade = simulate_one(
            bars,
            19,
            signal=Signal(Direction.BULLISH, (REASON,)),
            strategy=FakeStrategy(19, Direction.BULLISH, exits),
            market=FLAT_MARKET,
        )

        assert trade is not None
        assert "unavailable" in trade.against_news


class TestRunStrategy:
    def test_no_lookahead_is_possible_past_the_last_bar(self) -> None:
        """A signal on the final bar has nothing to enter on, so it is dropped
        rather than entered at a price that does not exist."""
        bars = _steady(20)
        exits = ExitPolicy(stop_in_atr=1.0, target_in_atr=1.5, max_sessions_held=5)
        strategy = FakeStrategy(len(bars) - 1, Direction.BULLISH, exits)

        assert run_strategy(strategy, bars, FLAT_MARKET, warmup=15) == []


class TestSignalsMustCarryEvidence:
    def test_a_neutral_signal_is_refused(self) -> None:
        with pytest.raises(ValueError, match="NEUTRAL"):
            Signal(direction=Direction.NEUTRAL, reasons=(REASON,))

    def test_a_signal_with_no_measured_reason_is_refused(self) -> None:
        """Rule 9. A signal that cannot say why it fired cannot be reviewed."""
        with pytest.raises(ValueError, match="measured reasons"):
            Signal(direction=Direction.BULLISH, reasons=())
