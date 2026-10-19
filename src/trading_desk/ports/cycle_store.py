"""What one research cycle has already done, kept somewhere that survives a crash.

Two things, one port, because they answer the same question and splitting them
would mean two stores to keep consistent:

1. **Node runs** - which nodes started, which finished, when. This is the
   delegation tree (FR-4.3) and it is also how a resumed cycle knows where it
   got to.
2. **Cached responses** - what the model said, keyed by cycle and by the exact
   call. This is the part that makes a resume free (FR-4.12).

**Why the cache is keyed by cycle and not global.** A global response cache
would be a different feature: it would make two *different* cycles share an
answer, which is wrong when the same prompt is asked on two different days
about two different market states. Scoping it to the cycle means it can only
ever replay work that was interrupted, never recycle yesterday's judgement.

Append-only, like the journal and the ledger. A checkpoint that can be edited
cannot answer "what did this cycle actually do?", which is the only question it
exists for.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from trading_desk.domain.cycle import NodeStatus


@dataclass(frozen=True)
class NodeRun:
    """One node, one attempt."""

    cycle_id: str
    node: str
    status: NodeStatus
    recorded_at: datetime
    detail: str = ""
    # The node's measured values, kept as strings. Enough to reconstruct the
    # delegation tree and to read in a trace; not enough to resume from, which
    # is deliberate - see the DEVLOG entry for 3.4.
    measurements: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.recorded_at.tzinfo is None:
            raise ValueError(f"{self.cycle_id}/{self.node}: recorded_at must be tz-aware")


@dataclass(frozen=True)
class CachedResponse:
    """One model reply, stored so an interrupted cycle does not pay twice.

    `payload` is the validated response model as JSON. Model output only - no
    prompt, no credential (rule 8).
    """

    cycle_id: str
    node: str
    call_key: str
    model: str
    payload: str
    input_tokens: int
    output_tokens: int


class CycleStore(Protocol):
    def record_node(self, run: NodeRun) -> None:
        """Append one node run. Never updates an existing row."""
        ...

    def node_runs(self, cycle_id: str) -> list[NodeRun]:
        """Every recorded attempt for this cycle, oldest first."""
        ...

    def save_response(self, response: CachedResponse) -> None:
        """Store a model reply against this cycle. Writing the same key twice
        is a no-op, not a second row: a replayed call must not look like a
        second call to anything reading this back."""
        ...

    def cached_response(self, cycle_id: str, call_key: str) -> CachedResponse | None:
        """The stored reply for this exact call in this cycle, or None."""
        ...
