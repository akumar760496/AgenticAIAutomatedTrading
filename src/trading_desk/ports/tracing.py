"""Recording what an agent did, so a briefing line can be walked backwards.

`docs/12` step 3.4 asks for exactly one thing from tracing: pick any line in a
briefing and walk back to the LLM calls and source documents that produced it.
If you cannot, tracing is incomplete. That requirement, not a vendor's feature
list, is what this port is shaped around.

A span is closed with its outcome whether the call succeeded or raised. A trace
that only records successes hides the failures, which are the ones worth
reading at 10pm.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from trading_desk.domain.values import Money


@dataclass(frozen=True)
class TraceSpan:
    """One recorded step. Prompts are included: a trace without the prompt
    cannot explain why the model said what it said."""

    cycle_id: str
    node: str
    model: str
    started_at: datetime
    ended_at: datetime
    input_tokens: int
    output_tokens: int
    cost: Money
    ok: bool
    detail: str = ""
    inputs: dict[str, Any] = field(default_factory=dict)


class Tracer(Protocol):
    def record(self, span: TraceSpan) -> None: ...
