"""One simulated trade, and why it ended the way it did.

The fields are chosen so that a trade can be read on its own, months later,
without re-running anything: what was bought or sold, at what price, when, on
what evidence, and what the market was doing at the time.

**`market_direction` is populated; `against_news` is not.** The index's own
bars reach back to 2023, so "was this trade with or against the market" is
answerable. The filings archive begins 2026-10-14 and holds six days, so
"was this trade against the news" is not - and it reads `unavailable` rather
than defaulting to "no", for the same reason `NO_DATA` exists in the gates.
Silence about a thing nobody looked at must not read like evidence of its
absence.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from trading_desk.domain.enums import Direction, TrendRegime
from trading_desk.domain.gates import GateResult
from trading_desk.domain.values import Money, Price

# Round trip on equity delivery: brokerage, STT, exchange fees, GST, stamp duty
# and slippage. Light on purpose - an options spread costs far more (ISS-023
# measured ~13% of a spread's best case), so this flatters rather than punishes.
DEFAULT_COST_FRACTION = 0.0025

NEWS_UNAVAILABLE = "unavailable (no news archive before 2026-10-14)"

# Below this much movement in ATR terms, a time-stopped trade went nowhere
# rather than went wrong. Half an ATR over up to four weeks is a dead signal.
FLAT_MOVE_IN_ATR = 0.5


class ExitReason(StrEnum):
    TARGET = "TARGET"
    STOP = "STOP"
    TIME = "TIME"


class FailureMode(StrEnum):
    """Why a losing trade lost. Mechanical, never inferred.

    Each one implicates a different parameter, which is the point: "it lost"
    is not actionable and "it gapped through the stop overnight" is.
    """

    STOPPED_IMMEDIATELY = "STOPPED_IMMEDIATELY"
    GAPPED_THROUGH_STOP = "GAPPED_THROUGH_STOP"
    STOPPED_LATER = "STOPPED_LATER"
    TIMED_OUT_FLAT = "TIMED_OUT_FLAT"
    TIMED_OUT_ADVERSE = "TIMED_OUT_ADVERSE"
    NOT_A_FAILURE = "NOT_A_FAILURE"

    def explain(self) -> str:
        return _FAILURE_NOTES[self]


# What happened, and the parameter it implicates. No trading advice: naming a
# fix for a losing strategy is how a backtest turns into a story.
_FAILURE_NOTES: dict[FailureMode, str] = {
    FailureMode.STOPPED_IMMEDIATELY: (
        "Stopped on the first session held. The stop sat inside one day's normal range, "
        "so entry-day volatility alone was enough to close it. Implicates stop_in_atr, "
        "or entering on the open after a signal day that already moved."
    ),
    FailureMode.GAPPED_THROUGH_STOP: (
        "The session opened beyond the stop, so the exit price is worse than the stop "
        "level. No stop placement prevents this; it is overnight risk, and it is the one "
        "failure a tighter stop makes worse rather than better."
    ),
    FailureMode.STOPPED_LATER: (
        "Ran for more than a session, then reversed into the stop. The entry was not "
        "immediately wrong, which implicates the exit rule rather than the signal."
    ),
    FailureMode.TIMED_OUT_FLAT: (
        "Reached the time stop having moved less than half an ATR either way. The signal "
        "selected a day that led nowhere - a selectivity problem, not an exit problem."
    ),
    FailureMode.TIMED_OUT_ADVERSE: (
        "Reached the time stop while losing, without hitting the stop. The move was "
        "against the trade but too slow to trigger an exit. Implicates max_sessions_held."
    ),
    FailureMode.NOT_A_FAILURE: "The trade finished in profit after costs.",
}


@dataclass(frozen=True)
class SimulatedTrade:
    """One entry and exit, with everything needed to judge it after the fact."""

    strategy: str
    symbol: str
    is_index: bool
    direction: Direction
    entry_date: str
    entry_price: Price
    exit_date: str
    exit_price: Price
    sessions_held: int
    exit_reason: ExitReason
    stop_level: Price
    target_level: Price
    atr_at_entry: Money
    # Why the strategy took it. Rule 9, carried per trade.
    reasons: tuple[GateResult, ...]
    # What the index was doing on the entry date, and whether this went with it.
    market_direction: TrendRegime
    against_market: bool
    against_news: str = NEWS_UNAVAILABLE
    cost_fraction: float = DEFAULT_COST_FRACTION

    @property
    def gross_return(self) -> float:
        """Signed return in the direction traded."""
        move = (self.exit_price.paise - self.entry_price.paise) / self.entry_price.paise
        return move if self.direction is Direction.BULLISH else -move

    @property
    def net_return(self) -> float:
        return self.gross_return - self.cost_fraction

    @property
    def won(self) -> bool:
        return self.net_return > 0

    @property
    def failure_mode(self) -> FailureMode:
        if self.won:
            return FailureMode.NOT_A_FAILURE
        if self.exit_reason is ExitReason.STOP:
            return self._stop_failure()
        if self.exit_reason is ExitReason.TARGET:
            # The target was reached and it still lost: costs ate a thin win.
            return FailureMode.TIMED_OUT_FLAT
        return self._time_failure()

    def _stop_failure(self) -> FailureMode:
        beyond_stop = (
            self.exit_price.paise < self.stop_level.paise
            if self.direction is Direction.BULLISH
            else self.exit_price.paise > self.stop_level.paise
        )
        if beyond_stop:
            return FailureMode.GAPPED_THROUGH_STOP
        if self.sessions_held <= 1:
            return FailureMode.STOPPED_IMMEDIATELY
        return FailureMode.STOPPED_LATER

    def _time_failure(self) -> FailureMode:
        if self.atr_at_entry.paise <= 0:
            return FailureMode.TIMED_OUT_FLAT
        moved = abs(self.exit_price.paise - self.entry_price.paise) / self.atr_at_entry.paise
        if moved < FLAT_MOVE_IN_ATR:
            return FailureMode.TIMED_OUT_FLAT
        return FailureMode.TIMED_OUT_ADVERSE
