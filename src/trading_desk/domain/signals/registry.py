"""The signals an agent is allowed to name (FR-5.10).

An agent that references a signal not listed here fails validation. The reason
is specific: an LLM will happily invent "the 9-day momentum crossover" and give
you a confident paragraph about it, and nothing downstream would notice. The
registry turns an invented signal into a loud error instead of a plausible one.

**Two levels, and the difference matters.**

- *Registered* means the signal exists, is implemented, and is computed the
  same way every time. `require_registered` enforces that.
- *Usable* means it has survived a backtest. `require_usable` enforces that,
  and **today nothing is usable**, because the historical dry run is step 2.8
  and has not been run. That is the honest state, not an oversight.

So the registry currently serves as a vocabulary, and becomes a gate once 2.8
produces evidence. `evidence` on each entry says what is actually known, and
`verify 2_2` fails if anything claims to be backtested while 2.8 is pending.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class SignalStatus(StrEnum):
    """How much is actually known about a signal."""

    # Implemented and deterministic, no out-of-sample evidence yet.
    IMPLEMENTED = "IMPLEMENTED"
    # Survived the step 2.8 dry run over available history.
    BACKTESTED = "BACKTESTED"
    # Tried and dropped. Kept so it is not re-proposed as a new idea.
    RETIRED = "RETIRED"


class UnknownSignalError(ValueError):
    """An agent named a signal that does not exist."""


class UnbackedSignalError(ValueError):
    """A registered signal was used before it had any evidence behind it."""


@dataclass(frozen=True)
class SignalDefinition:
    """One named signal, and the honest state of the evidence for it."""

    name: str
    description: str
    status: SignalStatus
    # Where the number comes from, so a reader can find the code.
    computed_by: str
    # What is actually known. Prose on purpose: "no evidence yet" is a
    # complete and useful answer, and a score would imply one that is not there.
    evidence: str


_DEFINITIONS = (
    SignalDefinition(
        name="atr_14",
        description="14-period average true range, Wilder-smoothed",
        status=SignalStatus.IMPLEMENTED,
        computed_by="domain.indicators.average_true_range",
        evidence="No backtest. Used as a volatility unit for stops, not as a signal.",
    ),
    SignalDefinition(
        name="range_20d",
        description="High-low range across the last 20 sessions",
        status=SignalStatus.IMPLEMENTED,
        computed_by="domain.indicators.price_range",
        evidence="No backtest. Descriptive.",
    ),
    SignalDefinition(
        name="volume_ratio_20d",
        description="Latest volume against the mean of the prior 20 sessions",
        status=SignalStatus.IMPLEMENTED,
        computed_by="domain.indicators.volume_ratio",
        evidence="No backtest. Volume expansion is a conventional confirm, untested here.",
    ),
    SignalDefinition(
        name="trend_regime_20_50",
        description="Uptrend / downtrend / ranging from the 20 and 50 session averages",
        status=SignalStatus.IMPLEMENTED,
        computed_by="domain.indicators.trend_regime",
        evidence="No backtest. Known to lag at turns; feeds a confirming gate only.",
    ),
    SignalDefinition(
        name="realised_vol_20d",
        description="Annualised close-to-close volatility over 20 sessions",
        status=SignalStatus.IMPLEMENTED,
        computed_by="domain.vol.realised_volatility",
        evidence="No backtest. Currently the documented IV-rank proxy (FR-5.6).",
    ),
    SignalDefinition(
        name="iv_rank",
        description="Percentile of today's ATM IV within the trailing window",
        status=SignalStatus.IMPLEMENTED,
        computed_by="domain.vol.rank_within",
        evidence=(
            "No backtest, and no real IV history yet either - the chain archive "
            "began 2026-10-16 and needs 30 sessions. Values are proxy-derived."
        ),
    ),
)

REGISTRY: dict[str, SignalDefinition] = {item.name: item for item in _DEFINITIONS}


def is_registered(name: str) -> bool:
    return name in REGISTRY


def require_registered(name: str) -> SignalDefinition:
    """The signal, or a loud error naming what is available.

    The error lists the valid names because the caller is often an agent whose
    output is being validated, and the list is what lets the next attempt
    succeed rather than guess again.
    """
    definition = REGISTRY.get(name)
    if definition is None:
        raise UnknownSignalError(
            f"{name!r} is not a registered signal. Known signals: {sorted(REGISTRY)}"
        )
    return definition


def require_usable(name: str) -> SignalDefinition:
    """A signal that is registered *and* has evidence behind it.

    Raises for everything today. That is correct: step 2.8 has not run, so no
    signal has out-of-sample evidence, and FR-5.10 says only pre-backtested
    signals may be referenced. Wiring an agent to this before 2.8 will fail
    loudly, which is the intended order of work.
    """
    definition = require_registered(name)
    if definition.status is not SignalStatus.BACKTESTED:
        raise UnbackedSignalError(
            f"{name!r} is {definition.status}, not BACKTESTED: {definition.evidence}"
        )
    return definition


def backtested_signals() -> list[str]:
    return sorted(name for name, item in REGISTRY.items() if item.status is SignalStatus.BACKTESTED)
