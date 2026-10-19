"""Where an idea stands against its own levels, at the close.

One pure function, and the reason it is pure is the reason it exists at all:
**the comparison is direction-sensitive.** A bullish idea has its target above
its stop; a bearish idea has them the other way round. Code that asks
`close >= target` reads correctly on every bullish example and then never fires
on a bearish one - a silent half-blindness that no amount of integration
testing over mostly-bullish ideas would reveal. Here it is one function with
both branches side by side, and the test suite drives both.

**This says nothing about profit or loss.** The levels are levels on the
*underlying*; the position is a spread whose value depends on time and implied
volatility as well as price. "Past target" means the underlying went where the
thesis said it would, which is what the research is being judged on. Money is
step 3.8's subject, and it needs the full cost model.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from trading_desk.domain.enums import Direction
from trading_desk.domain.values import Price


class IdeaStanding(StrEnum):
    """What the close says about an idea. Not an outcome - see the module docstring."""

    PAST_TARGET = "PAST_TARGET"
    PAST_STOP = "PAST_STOP"
    INSIDE_LEVELS = "INSIDE_LEVELS"
    EXPIRED = "EXPIRED"
    # Something needed was missing. Never guessed at (rule 6).
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class Standing:
    """The verdict and the numbers behind it.

    `detail` is not decoration. Rule 9: a comparison reports what it measured
    and what it compared against, so "INSIDE_LEVELS" in a report is always
    followed by the three figures that produced it.
    """

    standing: IdeaStanding
    detail: str

    @property
    def is_known(self) -> bool:
        return self.standing is not IdeaStanding.UNKNOWN


def standing_for(
    *,
    direction: Direction,
    close: Price | None,
    stop: Price | None,
    target: Price | None,
    expiry: date | None,
    today: date,
) -> Standing:
    """Where one idea stands at today's close.

    Expiry is checked first but only *after* the expiry date, so an idea is
    still measured against its levels on expiry day itself.
    """
    if expiry is not None and today > expiry:
        return Standing(IdeaStanding.EXPIRED, f"expiry {expiry:%d %b} has passed")

    if close is None:
        return Standing(IdeaStanding.UNKNOWN, f"no daily bar for {today:%d %b}")

    if stop is None or target is None:
        return Standing(
            IdeaStanding.UNKNOWN,
            f"close {close}, but no stop/target recorded for this idea",
        )

    inverted = _inverted_levels(direction, stop, target)
    if inverted is not None:
        return Standing(IdeaStanding.UNKNOWN, inverted)

    return _against_levels(direction, close, stop, target)


def _inverted_levels(direction: Direction, stop: Price, target: Price) -> str | None:
    """A complaint when the levels contradict the direction, or None.

    Worth its own check because inverted levels do not look like an error
    downstream: they make an idea read as past target and past stop at once,
    and whichever branch is written first wins silently.
    """
    if direction is Direction.BULLISH and target <= stop:
        return f"bullish idea with target {target} at or below stop {stop} - levels look wrong"
    if direction is Direction.BEARISH and target >= stop:
        return f"bearish idea with target {target} at or above stop {stop} - levels look wrong"
    return None


def _against_levels(direction: Direction, close: Price, stop: Price, target: Price) -> Standing:
    measured = f"close {close}, stop {stop}, target {target}"

    if direction is Direction.BEARISH:
        if close <= target:
            return Standing(IdeaStanding.PAST_TARGET, measured)
        if close >= stop:
            return Standing(IdeaStanding.PAST_STOP, measured)
        return Standing(IdeaStanding.INSIDE_LEVELS, measured)

    # BULLISH, and NEUTRAL with it: a neutral idea still carries a long and a
    # short strike, and the long strike is the level it is abandoned below.
    if close >= target:
        return Standing(IdeaStanding.PAST_TARGET, measured)
    if close <= stop:
        return Standing(IdeaStanding.PAST_STOP, measured)
    return Standing(IdeaStanding.INSIDE_LEVELS, measured)
