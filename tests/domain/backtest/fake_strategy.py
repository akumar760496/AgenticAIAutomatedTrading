"""A strategy that fires on a chosen bar, so engine tests test the engine.

Lives here rather than in `tests/fakes/` because that package holds one fake
per **port**, and `verify 0_6` counts them against the port inventory.
`Strategy` is a domain Protocol, not a port.

A real strategy decides *whether* to trade; the engine decides what happens
next. Testing the engine through a real strategy means arranging bars that
both produce a signal and exercise the exit path, and a failure then cannot
say which half broke.
"""

from __future__ import annotations

from collections.abc import Sequence

from trading_desk.domain.backtest.strategy import ExitPolicy, Signal
from trading_desk.domain.enums import Direction, Verdict
from trading_desk.domain.gates import GateResult
from trading_desk.domain.market import Bar

FIXED_REASON = GateResult("fake_signal", Verdict.PASS, "fires on request", "a chosen index")


class FakeStrategy:
    """Signals once, at `at_index`, in `direction`."""

    def __init__(
        self,
        at_index: int,
        direction: Direction = Direction.BULLISH,
        exits: ExitPolicy | None = None,
    ) -> None:
        self._at = at_index
        self._direction = direction
        self._exits = exits or ExitPolicy(stop_in_atr=1.0, target_in_atr=1.5, max_sessions_held=5)

    @property
    def name(self) -> str:
        return "fake_strategy"

    @property
    def trades_indices(self) -> bool:
        return False

    @property
    def exits(self) -> ExitPolicy:
        return self._exits

    def describe(self) -> str:
        return "a strategy that fires once, for testing the engine"

    def signal_at(self, bars: Sequence[Bar], index: int) -> Signal | None:
        del bars  # the whole point: the decision does not depend on the series
        if index != self._at:
            return None
        return Signal(direction=self._direction, reasons=(FIXED_REASON,))
