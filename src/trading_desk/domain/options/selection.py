"""Which structure fits: the volatility matrix from `docs/02`, as code.

> "Chooses *structure* from directional view + volatility view + event
> calendar: high IV rank into an event -> prefer defined-risk credit
> structures; low IV with a catalyst -> debit structures. **Encode that matrix
> as code**, with the LLM explaining and adjusting within it."

So it is encoded here rather than described in a prompt. The difference is not
style: a matrix in a prompt is re-decided on every call and drifts, and nothing
in the briefing can show you which cell was used.

Four outcomes, and refusing is one of them:

| IV rank                    | Answer                                      |
|----------------------------|---------------------------------------------|
| at or above `credit_above` | credit vertical - sell expensive premium    |
| at or below `debit_below`  | debit vertical - buy cheap premium          |
| in between                 | **nothing**, and the reason says so         |
| unknown                    | **nothing** (rule 6 - never guess)          |

The middle band is not an oversight. Between the two thresholds neither the
buyer nor the seller has an edge from volatility, and the honest answer is that
this setup does not apply - not "pick the one that matches the direction".

**`credit_above` may be None, and for Setup A it is.** Reading
`config/setups.yaml`: Setup A has one volatility number, `max_iv_rank: 60`, and
it is a ceiling on *buying* premium - the setup never sells. Only Setup B has
both sides (70 / 35). Passing None says "this setup has no credit answer", which
is different from "the threshold is very high" and reads differently in the
refusal reason.

Two hard defaults from `docs/02` are enforced here rather than left to config
reading: **defined-risk only** (both answers are verticals, which have a long
leg by construction - `domain/options/structure.py` raises rather than emit a
naked short), and **no short gamma on expiry day after 14:30**, which rules out
the credit answer in the last hour of an expiry session regardless of IV.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from enum import StrEnum

from trading_desk.domain.enums import Direction

# docs/02, hard default. A short vertical is short gamma, and on expiry
# afternoon gamma is large enough that a quiet position becomes a loss between
# two glances at the screen.
NO_SHORT_GAMMA_AFTER = time(14, 30)


class StructureKind(StrEnum):
    DEBIT_VERTICAL = "DEBIT_VERTICAL"
    CREDIT_VERTICAL = "CREDIT_VERTICAL"
    NONE = "NONE"


@dataclass(frozen=True)
class StructureChoice:
    """The chosen structure and the measured values that chose it (rule 9)."""

    kind: StructureKind
    reason: str
    iv_rank: float | None

    @property
    def is_tradable(self) -> bool:
        return self.kind is not StructureKind.NONE


def choose_structure(
    *,
    iv_rank: float | None,
    direction: Direction,
    credit_above: float | None,
    debit_below: float,
    is_expiry_day: bool = False,
    time_of_day: time | None = None,
) -> StructureChoice:
    """Pick a structure, or refuse and say why.

    `time_of_day` is passed in rather than read, because domain code never
    touches a clock (rule 3). It is only consulted on an expiry day.
    """
    if direction is Direction.NEUTRAL:
        return StructureChoice(
            kind=StructureKind.NONE,
            reason="a vertical needs a direction; NEUTRAL has none",
            iv_rank=iv_rank,
        )

    if iv_rank is None:
        return StructureChoice(
            kind=StructureKind.NONE,
            reason="no IV rank available, so there is no volatility view to trade",
            iv_rank=None,
        )

    if credit_above is not None and iv_rank >= credit_above:
        if _short_gamma_is_banned(is_expiry_day, time_of_day):
            return StructureChoice(
                kind=StructureKind.NONE,
                reason=(
                    f"IV rank {iv_rank:.0f} says sell premium, but it is expiry day "
                    f"after {NO_SHORT_GAMMA_AFTER:%H:%M} and a credit vertical is short gamma"
                ),
                iv_rank=iv_rank,
            )
        return StructureChoice(
            kind=StructureKind.CREDIT_VERTICAL,
            reason=f"IV rank {iv_rank:.0f} is at or above {credit_above:.0f}: premium is rich",
            iv_rank=iv_rank,
        )

    if iv_rank <= debit_below:
        return StructureChoice(
            kind=StructureKind.DEBIT_VERTICAL,
            reason=f"IV rank {iv_rank:.0f} is at or below {debit_below:.0f}: premium is cheap",
            iv_rank=iv_rank,
        )

    return _no_edge(iv_rank, credit_above, debit_below)


def _no_edge(iv_rank: float, credit_above: float | None, debit_below: float) -> StructureChoice:
    """Too dear to buy, not dear enough to sell - or not sellable at all."""
    if credit_above is None:
        reason = (
            f"IV rank {iv_rank:.0f} is above the {debit_below:.0f} ceiling: too "
            "expensive to buy premium, and this setup does not sell it"
        )
    else:
        reason = (
            f"IV rank {iv_rank:.0f} is between {debit_below:.0f} and {credit_above:.0f}: "
            "neither buying nor selling premium has an edge here"
        )
    return StructureChoice(kind=StructureKind.NONE, reason=reason, iv_rank=iv_rank)


def _short_gamma_is_banned(is_expiry_day: bool, time_of_day: time | None) -> bool:
    """Expiry afternoon. Unknown time on an expiry day counts as banned."""
    if not is_expiry_day:
        return False
    if time_of_day is None:
        return True
    return time_of_day >= NO_SHORT_GAMMA_AFTER
