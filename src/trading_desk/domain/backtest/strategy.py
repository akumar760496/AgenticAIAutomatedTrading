"""What a strategy is, and what it has to answer.

A strategy is deliberately small: given a series of bars and a position in it,
say whether to trade, which way, and **on what measured evidence**. Everything
else - how trades are simulated, what they cost, how failures are classified -
is the engine's job, identically for every strategy.

**Each strategy carries its own exit policy.** The index strategy and the stock
strategy do not share thresholds, because ISS-039 measured the same breakout
rule at 37% on stocks and 23% on indices. A shared parameter set would make one
of them wrong by construction.

**A signal returns `GateResult`s, never a boolean** (CLAUDE.md rule 9). That is
what lets the dashboard answer "why was this trade taken" for any individual
trade, months later, without re-running anything.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from trading_desk.domain.enums import Direction
from trading_desk.domain.gates import GateResult
from trading_desk.domain.market import Bar


@dataclass(frozen=True)
class ExitPolicy:
    """How a trade ends. Owned by the strategy, applied by the engine.

    Expressed in ATR multiples rather than percentages so the same policy means
    the same thing on a Rs 300 stock and a Rs 25,000 index.
    """

    stop_in_atr: float
    target_in_atr: float
    max_sessions_held: int

    def __post_init__(self) -> None:
        if self.stop_in_atr <= 0 or self.target_in_atr <= 0:
            raise ValueError("stop and target must both be positive ATR multiples")
        if self.max_sessions_held <= 0:
            raise ValueError(f"max_sessions_held must be positive, got {self.max_sessions_held}")

    @property
    def breakeven_win_rate(self) -> float:
        """The win rate this policy needs just to stand still, before costs.

        Printed beside every result on purpose. A 37% win rate reads as poor or
        excellent depending entirely on this number, and quoting one without
        the other is the most common way a backtest misleads.
        """
        return self.stop_in_atr / (self.stop_in_atr + self.target_in_atr)

    def describe(self) -> str:
        return (
            f"stop {self.stop_in_atr}x ATR, target {self.target_in_atr}x ATR, "
            f"time stop {self.max_sessions_held} sessions"
        )


@dataclass(frozen=True)
class Signal:
    """A strategy's decision to trade, and the evidence for it."""

    direction: Direction
    # Every criterion the strategy consulted, passing or not. Kept whole rather
    # than filtered to the passing ones: "it fired despite volume at 1.51x" is
    # the kind of thing only the full list can tell you.
    reasons: tuple[GateResult, ...]

    def __post_init__(self) -> None:
        if self.direction is Direction.NEUTRAL:
            raise ValueError("a signal cannot be NEUTRAL - that is no signal")
        if not self.reasons:
            raise ValueError(f"{self.direction} signal carries no measured reasons (rule 9)")

    def summary(self) -> str:
        return "; ".join(reason.describe() for reason in self.reasons)


class Strategy(Protocol):
    """One strategy. Add a new one by writing this and nothing else.

    `name` is what appears in the dashboard and in the results file, so it is
    stable: renaming it orphans every stored result that used the old name.
    """

    @property
    def name(self) -> str: ...

    @property
    def trades_indices(self) -> bool:
        """True for an index strategy, False for a stock strategy.

        A plain flag rather than a taxonomy: there are two kinds, they are
        traded differently, and a third kind would be a new question anyway.
        """
        ...

    @property
    def exits(self) -> ExitPolicy: ...

    def describe(self) -> str:
        """One sentence, shown at the top of the dashboard."""
        ...

    def signal_at(self, bars: Sequence[Bar], index: int) -> Signal | None:
        """Whether to enter on the close of `bars[index]`.

        Implementations must look at `bars[: index + 1]` only. Reading past
        `index` is lookahead, and it is the one bug that makes every number
        downstream meaningless.
        """
        ...
