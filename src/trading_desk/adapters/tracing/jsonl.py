"""Traces to a local JSONL file, one span per line.

**Not Langfuse, and that is a deliberate gap.** `docs/10` names Langfuse, which
needs either a hosted account or a self-hosted stack - a signup decision, and
this project does not make those on its own. The requirement Langfuse exists to
satisfy here is `docs/12` step 3.4: walk back from any briefing line to the
calls that produced it. A JSONL file satisfies that today, at no cost and with
no third party holding our prompts.

Swapping in a Langfuse adapter later means writing one class against
`ports.tracing.Tracer`; nothing upstream changes.

One file per day, appended. A span is written as it completes, not buffered,
because the run most worth tracing is the one that crashed.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from trading_desk.ports.tracing import TraceSpan

DEFAULT_TRACE_DIR = Path("data/traces")


class JsonlTracer:
    def __init__(self, directory: Path = DEFAULT_TRACE_DIR) -> None:
        self._directory = directory

    def record(self, span: TraceSpan) -> None:
        self._directory.mkdir(parents=True, exist_ok=True)
        path = self._directory / f"{span.started_at.date().isoformat()}.jsonl"
        payload = asdict(span)
        payload["started_at"] = span.started_at.isoformat()
        payload["ended_at"] = span.ended_at.isoformat()
        # Money serialises as {"paise": N}; flatten it so a trace line can be
        # grepped for a rupee figure without a JSON parser.
        payload["cost_paise"] = span.cost.paise
        payload.pop("cost", None)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, default=str) + "\n")
