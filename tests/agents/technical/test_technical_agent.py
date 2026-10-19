"""The Technical agent, over bars a test supplies.

Two claims are worth proving here and neither is about a formula - the
formulas are tested in `tests/domain/test_indicators.py`. They are: an
unregistered signal cannot be reached, and a short history produces `None`
with a reason rather than a number.
"""

from __future__ import annotations

import pytest

from tests.agents.fixtures import flat_bars, with_breakout
from tests.fakes import FakeBarStore
from trading_desk.agents.technical.agent import (
    BARS_NEEDED,
    SIGNALS_USED,
    TechnicalAgent,
)
from trading_desk.domain.enums import TrendRegime
from trading_desk.domain.signals.registry import REGISTRY, UnknownSignalError, require_registered


def _agent(bars_held: int, **overrides: object) -> TechnicalAgent:
    store = FakeBarStore()
    store.write_daily_bars(flat_bars(bars_held), "test")
    return TechnicalAgent(store, **overrides)  # type: ignore[arg-type]


class TestTheSignalLibraryIsClosed:
    def test_every_signal_the_agent_uses_is_registered(self) -> None:
        for name in SIGNALS_USED:
            assert require_registered(name).name == name

    def test_an_invented_signal_cannot_be_registered(self) -> None:
        with pytest.raises(UnknownSignalError, match="golden_cross_v2"):
            require_registered("golden_cross_v2")

    def test_the_agent_reports_that_none_of_them_is_backtested(self) -> None:
        """FR-5.10 is met by disclosure plus a closed list, not by pretending.
        If this ever reads 4 of 4, step 2.8 registered them and the briefing
        should start saying so."""
        agent = _agent(BARS_NEEDED)

        assert agent.backtested_count == 0
        view = agent.assess("RELIANCE")
        assert view.finding.measurements["signals_backtested"] == "0 of 4"

    def test_the_registry_knows_more_signals_than_this_agent_uses(self) -> None:
        """Selection from a library, not "use everything available"."""
        assert set(SIGNALS_USED) < set(REGISTRY)


class TestShortHistoryRefusesRatherThanGuesses:
    def test_no_bars_produces_no_numbers(self) -> None:
        view = TechnicalAgent(FakeBarStore()).assess("RELIANCE")

        assert view.bars_read == 0
        assert view.atr is None
        assert view.volume_ratio is None
        assert view.trend is TrendRegime.INSUFFICIENT_HISTORY
        assert view.finding.unavailable

    def test_a_short_history_says_how_short(self) -> None:
        view = _agent(10).assess("RELIANCE")

        assert not view.has_enough_history
        assert any("10 bars held" in item for item in view.finding.unavailable)

    def test_an_unmeasurable_value_is_none_and_never_zero(self) -> None:
        view = _agent(5).assess("RELIANCE")

        assert view.volume_ratio is None
        assert view.finding.measurements["volume_ratio_20d"] == "unavailable"


class TestMeasurements:
    def test_a_flat_series_has_no_trend_and_a_ratio_of_one(self) -> None:
        view = _agent(BARS_NEEDED).assess("RELIANCE")

        assert view.trend is TrendRegime.RANGING
        assert view.volume_ratio == pytest.approx(1.0)
        assert view.close_beyond_range_edge is False

    def test_a_close_above_the_prior_range_is_detected(self) -> None:
        store = FakeBarStore()
        store.write_daily_bars(
            with_breakout(flat_bars(BARS_NEEDED), close="1500", volume=300_000), "test"
        )
        view = TechnicalAgent(store).assess("RELIANCE")

        assert view.close_beyond_range_edge is True
        assert view.volume_ratio == pytest.approx(3.0)

    def test_the_breakout_window_excludes_today(self) -> None:
        """A close compared against a range that contains it can never be
        outside it, and the gate would silently never fire."""
        store = FakeBarStore()
        store.write_daily_bars(
            with_breakout(flat_bars(BARS_NEEDED), close="1500", volume=100_000), "test"
        )
        view = TechnicalAgent(store).assess("RELIANCE")

        assert view.close_beyond_range_edge is True

    def test_the_agent_measures_and_never_vetoes(self) -> None:
        """Comparing against a threshold is Setup A's job. Doing it here too
        would mean the same judgement made twice, from two sets of numbers."""
        view = _agent(BARS_NEEDED).assess("RELIANCE")

        assert view.finding.vetoes == ()
