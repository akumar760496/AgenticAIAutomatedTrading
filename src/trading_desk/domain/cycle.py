"""The nodes of one research cycle, and what happened at each.

Names rather than strings, because `cycle_id` + node is the join key for every
artifact a cycle produces - the ledger, the traces, the checkpoints and the
journal all key on the same two values (FR-4.1). A typo in a node name would
not break anything; it would quietly split one node's history in two.
"""

from __future__ import annotations

from enum import StrEnum


class CycleNode(StrEnum):
    """The six nodes. Five gather, one writes.

    The first five are independent of each other - none reads another's output
    - which is what makes them a fan-out. SYNTHESIS runs last and is the only
    one that costs money.
    """

    NEWS = "news"
    FUNDAMENTAL = "fundamental"
    TECHNICAL = "technical"
    OPTIONS = "options"
    PORTFOLIO = "portfolio"
    SYNTHESIS = "synthesis"


class NodeStatus(StrEnum):
    """STARTED is recorded before the work, not after.

    A node that crashes leaves a STARTED row and no COMPLETED row, which is the
    only way the next run can tell "this never finished" from "this was never
    reached" - and those need different handling (rule 6).
    """

    STARTED = "STARTED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


SPECIALIST_NODES = (
    CycleNode.NEWS,
    CycleNode.FUNDAMENTAL,
    CycleNode.TECHNICAL,
    CycleNode.OPTIONS,
    CycleNode.PORTFOLIO,
)
