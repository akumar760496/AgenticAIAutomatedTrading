"""What a gate returns. Never a bare boolean.

CLAUDE.md rule 9: **gates return values, not booleans.** A gate that answers
only "no" cannot be debugged at 10pm, cannot be scored later, and cannot tell
you it was one basis point away. Every result below carries what it measured
and what it was compared against, and the briefing prints both.

`NO_DATA` is deliberately distinct from `FAIL`. "IV rank 72, needed under 60"
and "no IV rank available" are different facts: the first is a judgement, the
second is a gap. Collapsing them hides the gap, and the gap is the thing that
gets fixed.

The combinator **does not short-circuit**. Evaluating every gate costs
nothing here and is what lets the briefing show the full picture - including
that an idea failed six gates rather than one, which is a different story.
"""

from __future__ import annotations

from dataclasses import dataclass

from trading_desk.domain.enums import Verdict


@dataclass(frozen=True)
class GateResult:
    """One gate's answer, with the numbers behind it."""

    name: str
    verdict: Verdict
    measured: str
    threshold: str
    detail: str = ""

    @property
    def passed(self) -> bool:
        return self.verdict is Verdict.PASS

    @property
    def blocking(self) -> bool:
        """Whether this result stops an idea. Missing data blocks, by rule 6."""
        return self.verdict is not Verdict.PASS

    def describe(self) -> str:
        return f"{self.name}: {self.verdict} (measured {self.measured}, needed {self.threshold})"


def passed(name: str, measured: str, threshold: str, detail: str = "") -> GateResult:
    return GateResult(name, Verdict.PASS, measured, threshold, detail)


def failed(name: str, measured: str, threshold: str, detail: str = "") -> GateResult:
    return GateResult(name, Verdict.FAIL, measured, threshold, detail)


def no_data(name: str, threshold: str, detail: str = "") -> GateResult:
    """Distinct from a failure: we could not judge, rather than judged no."""
    return GateResult(name, Verdict.NO_DATA, "unavailable", threshold, detail)


@dataclass(frozen=True)
class GateReport:
    """Every gate's result for one candidate, in evaluation order."""

    instrument_symbol: str
    results: tuple[GateResult, ...]

    @property
    def approved(self) -> bool:
        return bool(self.results) and all(result.passed for result in self.results)

    def blockers(self) -> tuple[GateResult, ...]:
        return tuple(result for result in self.results if result.blocking)

    def first_blocker(self) -> GateResult | None:
        """The gate to name in a rejection. The first is the most informative:
        gates are ordered cheapest and most-discriminating first."""
        blockers = self.blockers()
        return blockers[0] if blockers else None

    def values(self) -> dict[str, str]:
        """Every gate's measured value, for the journal (rule 9)."""
        return {result.name: result.measured for result in self.results}

    def summary(self) -> str:
        if self.approved:
            return f"{self.instrument_symbol}: all {len(self.results)} gates passed"
        blockers = self.blockers()
        named = ", ".join(result.name for result in blockers)
        return f"{self.instrument_symbol}: {len(blockers)}/{len(self.results)} blocked ({named})"
