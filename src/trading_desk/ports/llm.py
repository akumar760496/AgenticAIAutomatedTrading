"""What we need from a language model.

Structured output only. An agent returns a validated object or the cycle fails -
there is no path where prose reaches a decision. Cost is returned with every call
because the monthly cap refuses calls rather than alerting after the fact.

This port is the *model* and nothing else. It does not know the budget: asking a
client how much we have spent gives a different answer per process, and a cap
that resets on restart is not a monthly cap. Spend lives in `ports/llm_ledger.py`
and the two are combined by `application.llm_governor`, which is what agents are
handed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, TypeVar

from pydantic import BaseModel

from trading_desk.domain.llm_cost import ModelTier
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


class GovernedLlmPort(Protocol):
    """A model call that is already budgeted, recorded and traced.

    This is what an agent is given. It exists as a port so `agents/` can depend
    on an interface rather than on `application.llm_governor`, keeping the
    import-linter contract "agents may import domain and ports only" honest
    rather than merely unviolated.

    Every call names its node and cycle. Without those the per-node cost report
    cannot be built, and a briefing that cannot say what it cost is not
    auditable - so they are required arguments, not optional context.
    """

    def complete[T: BaseModel](
        self,
        *,
        node: str,
        cycle_id: str,
        tier: ModelTier,
        system_prompt: str,
        user_content: str,
        response_model: type[T],
    ) -> LlmResult[T]:
        """Make one governed call, or raise if the month's cap would be crossed."""
        ...
