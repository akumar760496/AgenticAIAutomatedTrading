"""What a specialist agent is allowed to hand back.

Four specialists answer four different questions about one symbol, and the
Chief Strategist in step 3.4 has to merge them. One shared shape means it can
do that without four special cases, and - more usefully - means all four are
forced to answer the same three questions about themselves:

- **What did you measure?** `measurements`, values not verdicts (rule 9). A
  gate that reports PASS has thrown away the number that would have told you,
  a year later, whether the threshold was in the right place.
- **What kills this idea?** `vetoes`. Empty is the normal case.
- **What could you not get?** `unavailable`. This is the field that exists
  because of CLAUDE.md rule 6. An agent that cannot compute a number says so;
  it never returns a partial figure that reads like a complete one. "net delta
  unavailable: 2 of 5 positions have no chain quote" is a usable sentence.
  "net delta 0.3" computed from the three that did is a wrong briefing.

There is deliberately no confidence field and no score. A specialist reports;
ranking is the strategist's job and sizing is the risk gate's.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class SpecialistName(StrEnum):
    FUNDAMENTAL = "fundamental"
    TECHNICAL = "technical"
    OPTIONS = "options"
    PORTFOLIO = "portfolio"


@dataclass(frozen=True)
class SpecialistFinding:
    """One specialist's answer about one symbol."""

    specialist: SpecialistName
    symbol: str
    measurements: dict[str, str] = field(default_factory=dict)
    vetoes: tuple[str, ...] = ()
    unavailable: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.symbol.strip():
            raise ValueError(f"{self.specialist}: a finding must name its symbol")
        for veto in self.vetoes:
            if not veto.strip():
                raise ValueError(f"{self.specialist}/{self.symbol}: a veto must say why")
        for missing in self.unavailable:
            if not missing.strip():
                raise ValueError(
                    f"{self.specialist}/{self.symbol}: a missing input must say what is missing"
                )

    @property
    def is_clear(self) -> bool:
        """No veto. Says nothing about whether anything was measured."""
        return not self.vetoes

    def summary(self) -> str:
        parts = [f"{self.specialist}/{self.symbol}: {len(self.measurements)} measured"]
        if self.vetoes:
            parts.append(f"{len(self.vetoes)} veto(es): {'; '.join(self.vetoes)}")
        if self.unavailable:
            parts.append(f"unavailable: {'; '.join(self.unavailable)}")
        return " | ".join(parts)
