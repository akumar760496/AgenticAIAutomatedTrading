"""What we need from a language model.

Structured output only. An agent returns a validated object or the cycle fails -
there is no path where prose reaches a decision. Cost is returned with every call
because the monthly cap refuses calls rather than alerting after the fact.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, TypeVar

from pydantic import BaseModel

from trading_desk.domain.values import Money

ResponseModel = TypeVar("ResponseModel", bound=BaseModel)


@dataclass(frozen=True)
class LlmUsage:
    """What one call consumed. Summed per cycle and shown in every briefing."""

    model: str
    input_tokens: int
    output_tokens: int
    cost: Money


@dataclass(frozen=True)
class LlmResult[T: BaseModel]:
    """A validated response plus what it cost to get."""

    value: T
    usage: LlmUsage


class LlmPort(Protocol):
    """A model that returns validated structured output."""

    def complete(
        self,
        system_prompt: str,
        user_content: str,
        response_model: type[ResponseModel],
        model: str,
    ) -> LlmResult[ResponseModel]:
        """Call the model and parse into `response_model`.

        Raises on a response that does not validate. It never returns a partial or
        best-effort object: a malformed agent response fails the cycle rather than
        degrading the briefing, because a briefing nobody can trust is worse than
        no briefing.
        """
        ...

    def spend_this_month(self) -> Money:
        """Total spend in the current calendar month, for the cap check."""
        ...
