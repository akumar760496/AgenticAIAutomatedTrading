"""In-memory node runs and cached responses, append-only like the real store."""

from __future__ import annotations

from trading_desk.ports.cycle_store import CachedResponse, NodeRun


class FakeCycleStore:
    """Keeps every run in order, and one response per (cycle, call).

    `crash_after` exists so a test can stop a cycle mid-flight without killing
    the process: set it to a node name and that node raises. Resume is only
    testable if there is a way to not finish.
    """

    def __init__(self) -> None:
        self.runs: list[NodeRun] = []
        self.responses: dict[tuple[str, str], CachedResponse] = {}
        self.replays = 0

    def record_node(self, run: NodeRun) -> None:
        self.runs.append(run)

    def node_runs(self, cycle_id: str) -> list[NodeRun]:
        return [run for run in self.runs if run.cycle_id == cycle_id]

    def save_response(self, response: CachedResponse) -> None:
        key = (response.cycle_id, response.call_key)
        if key in self.responses:
            return
        self.responses[key] = response

    def cached_response(self, cycle_id: str, call_key: str) -> CachedResponse | None:
        found = self.responses.get((cycle_id, call_key))
        if found is not None:
            self.replays += 1
        return found
