"""Turning a passed candidate into two readable sentences.

The one LLM node in the strategist, on the synthesis tier, and it runs **after
ranking** so a cycle pays for at most two calls however many candidates it
looked at.

Three things are not the model's to decide, and none of them is a field it can
fill in:

1. **Direction, structure and every gate value** are already fixed. They are
   passed in as facts and copied into the briefing unchanged.
2. **Numbers.** Every figure in what it writes must appear in the facts it was
   given, checked by `domain/grounding.py`. An invented number reads exactly
   like a measured one, which is the whole problem.
3. **Size.** There is no field for it, and the prompt says so twice.

An ungrounded number **drops the idea** rather than failing the cycle. Failing
would mean no briefing at all because one sentence was wrong; dropping leaves
the other idea standing and puts the reason in the rejected list, where it is
visible (FR-4.10).
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, Field

from trading_desk.agents.strategist.prompts import SYNTHESIS_SYSTEM
from trading_desk.domain.grounding import ungrounded_numbers
from trading_desk.domain.llm_cost import ModelTier
from trading_desk.ports.llm import GovernedLlmPort

NODE = "synthesis"

MAX_CLAIM = 300
MAX_INVALIDATION = 250
MAX_RATIONALE = 400


class SynthesisResponse(BaseModel):
    """Three strings. No direction, no level, no size - see the module docstring."""

    claim: str = Field(min_length=1, max_length=MAX_CLAIM)
    invalidation: str = Field(min_length=1, max_length=MAX_INVALIDATION)
    rationale: str = Field(default="", max_length=MAX_RATIONALE)


@dataclass(frozen=True)
class Synthesis:
    """What the model wrote, plus whether it stayed inside the facts."""

    claim: str
    invalidation: str
    rationale: str
    invented_numbers: tuple[str, ...]

    @property
    def is_grounded(self) -> bool:
        return not self.invented_numbers


class SynthesisWriter:
    def __init__(self, llm: GovernedLlmPort) -> None:
        self._llm = llm

    def write(self, *, facts: str, symbol: str, cycle_id: str) -> Synthesis:
        result = self._llm.complete(
            node=NODE,
            cycle_id=cycle_id,
            tier=ModelTier.SYNTHESIS,
            system_prompt=SYNTHESIS_SYSTEM,
            user_content=f"Instrument: {symbol}\n\nFacts:\n{facts}",
            response_model=SynthesisResponse,
        )
        response = result.value
        written = f"{response.claim} {response.invalidation} {response.rationale}"
        return Synthesis(
            claim=response.claim,
            invalidation=response.invalidation,
            rationale=response.rationale,
            invented_numbers=tuple(ungrounded_numbers(written, facts)),
        )
