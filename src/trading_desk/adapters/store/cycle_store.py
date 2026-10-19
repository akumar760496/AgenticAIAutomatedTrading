"""Cycle checkpoints in Postgres, against the 0007 tables.

`save_response` relies on the unique constraint plus ON CONFLICT DO NOTHING
rather than reading first and writing second. A read-then-write has a race, and
the thing it would race on is the one row whose duplication would make a
resumed cycle look like it had paid twice.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from trading_desk.domain.cycle import NodeStatus
from trading_desk.infra.db import session_scope
from trading_desk.ports.cycle_store import CachedResponse, NodeRun

INSERT_NODE = text(
    """
    INSERT INTO cycle_nodes (cycle_id, node, status, detail, measurements, recorded_at)
    VALUES (:cycle_id, :node, :status, :detail, CAST(:measurements AS json), :recorded_at)
    """
)

SELECT_NODES = text(
    """
    SELECT cycle_id, node, status, detail, measurements, recorded_at
    FROM cycle_nodes WHERE cycle_id = :cycle_id ORDER BY run_id
    """
)

INSERT_RESPONSE = text(
    """
    INSERT INTO cycle_responses
        (cycle_id, node, call_key, model, payload, input_tokens, output_tokens)
    VALUES (:cycle_id, :node, :call_key, :model, :payload, :input_tokens, :output_tokens)
    ON CONFLICT (cycle_id, call_key) DO NOTHING
    """
)

SELECT_RESPONSE = text(
    """
    SELECT cycle_id, node, call_key, model, payload, input_tokens, output_tokens
    FROM cycle_responses WHERE cycle_id = :cycle_id AND call_key = :call_key
    """
)


class PostgresCycleStore:
    """Implements CycleStore against the cycle_nodes and cycle_responses tables."""

    def __init__(self, factory: sessionmaker[Session]) -> None:
        self._factory = factory

    def record_node(self, run: NodeRun) -> None:
        with session_scope(self._factory) as session:
            session.execute(
                INSERT_NODE,
                {
                    "cycle_id": run.cycle_id,
                    "node": run.node,
                    "status": run.status.value,
                    "detail": run.detail,
                    "measurements": json.dumps(run.measurements),
                    "recorded_at": run.recorded_at,
                },
            )

    def node_runs(self, cycle_id: str) -> list[NodeRun]:
        with session_scope(self._factory) as session:
            rows = session.execute(SELECT_NODES, {"cycle_id": cycle_id}).mappings().all()
        return [_to_run(dict(row)) for row in rows]

    def save_response(self, response: CachedResponse) -> None:
        with session_scope(self._factory) as session:
            session.execute(
                INSERT_RESPONSE,
                {
                    "cycle_id": response.cycle_id,
                    "node": response.node,
                    "call_key": response.call_key,
                    "model": response.model,
                    "payload": response.payload,
                    "input_tokens": response.input_tokens,
                    "output_tokens": response.output_tokens,
                },
            )

    def cached_response(self, cycle_id: str, call_key: str) -> CachedResponse | None:
        with session_scope(self._factory) as session:
            row = (
                session.execute(SELECT_RESPONSE, {"cycle_id": cycle_id, "call_key": call_key})
                .mappings()
                .first()
            )
        if row is None:
            return None
        return CachedResponse(
            cycle_id=str(row["cycle_id"]),
            node=str(row["node"]),
            call_key=str(row["call_key"]),
            model=str(row["model"]),
            payload=str(row["payload"]),
            input_tokens=int(row["input_tokens"]),
            output_tokens=int(row["output_tokens"]),
        )


def _to_run(row: dict[str, Any]) -> NodeRun:
    measurements = row.get("measurements") or {}
    return NodeRun(
        cycle_id=str(row["cycle_id"]),
        node=str(row["node"]),
        status=NodeStatus(str(row["status"])),
        recorded_at=row["recorded_at"],
        detail=str(row["detail"]),
        measurements={str(key): str(value) for key, value in dict(measurements).items()},
    )
