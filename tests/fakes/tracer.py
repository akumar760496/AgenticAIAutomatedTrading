"""A tracer that keeps spans in a list."""

from __future__ import annotations

from trading_desk.ports.tracing import TraceSpan


class FakeTracer:
    def __init__(self) -> None:
        self.spans: list[TraceSpan] = []

    def record(self, span: TraceSpan) -> None:
        self.spans.append(span)
