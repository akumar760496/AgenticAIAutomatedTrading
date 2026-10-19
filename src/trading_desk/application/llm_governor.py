"""The only way an agent is allowed to call a model.

Three things happen around every call, in this order, and the order is the whole
design:

1. **Estimate, then check the cap, then refuse.** The estimate is deliberately
   pessimistic (`domain.llm_cost`), because the alternative - checking after the
   call - is a receipt rather than a limit. `docs/10` step 3.1 says the cap must
   refuse, and a refusal that happens after the money is gone is not one.
2. **Call the model.** Transport, retries and parsing are the adapter's job.
3. **Record and trace, whatever happened.** A failed call still costs input
   tokens on some failure modes, and a trace that only holds successes hides
   exactly the runs worth reading.

Agents are handed this class, never a raw client. That is enforced by
construction rather than by a rule: nothing else builds an Anthropic client.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from pydantic import BaseModel

from trading_desk.domain.llm_cost import (
    CapVerdict,
    LlmPricing,
    ModelTier,
    MonthlyCapExceededError,
    check_cap,
    estimate_cost,
)
from trading_desk.domain.timezones import to_ist
from trading_desk.domain.values import Money
from trading_desk.ports.clock import Clock
from trading_desk.ports.llm import LlmPort, LlmResult
from trading_desk.ports.llm_ledger import LedgerEntry, LlmLedger
from trading_desk.ports.tracing import Tracer, TraceSpan


@dataclass(frozen=True)
class NodeCost:
    """What one agent node cost across a cycle."""

    node: str
    calls: int
    input_tokens: int
    output_tokens: int
    cost: Money


@dataclass(frozen=True)
class CycleCostReport:
    """Cost per node and in total, for one research cycle.

    Per node rather than only a total, because a total tells you the bill and
    per-node tells you which agent to fix.
    """

    cycle_id: str
    nodes: list[NodeCost]
    total: Money

    def as_lines(self) -> list[str]:
        lines = [f"{node.node:<24} {node.calls:>3} calls  {node.cost}" for node in self.nodes]
        lines.append(f"{'TOTAL':<24} {sum(n.calls for n in self.nodes):>3} calls  {self.total}")
        return lines


class CostGovernedLlm:
    """An LLM that refuses to overspend."""

    def __init__(
        self,
        client: LlmPort,
        ledger: LlmLedger,
        pricing: LlmPricing,
        clock: Clock,
        tracer: Tracer | None = None,
    ) -> None:
        self._client = client
        self._ledger = ledger
        self._pricing = pricing
        self._clock = clock
        self._tracer = tracer

    def spend_this_month(self) -> Money:
        """Spend so far in the current IST calendar month."""
        today = to_ist(self._clock.utc_now()).date()
        return self._ledger.spend_in_month(today.year, today.month)

    def budget_check(self, tier: ModelTier, system_prompt: str, user_content: str) -> CapVerdict:
        """What the cap would say about this call, without making it.

        Public so a cycle can decide up front whether it can afford to run at
        all, rather than failing halfway and leaving a part-finished briefing.
        """
        estimated = estimate_cost(self._pricing, tier, system_prompt, user_content)
        return check_cap(self.spend_this_month(), self._pricing.monthly_cap, estimated)

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
        """Make one governed call. Raises `MonthlyCapExceededError` if it would not fit."""
        verdict = self.budget_check(tier, system_prompt, user_content)
        if not verdict.allowed:
            raise MonthlyCapExceededError(verdict)

        model = self._pricing.model_for(tier)
        started_at = self._clock.utc_now()
        try:
            result = self._client.complete(
                system_prompt=system_prompt,
                user_content=user_content,
                response_model=response_model,
                model=model,
            )
        except Exception as error:
            self._trace_failure(node, cycle_id, model, started_at, error)
            raise

        self._record(
            node=node,
            cycle_id=cycle_id,
            tier=tier,
            model=model,
            result=result,
            started_at=started_at,
        )
        return result

    def cost_report(self, cycle_id: str) -> CycleCostReport:
        """Cost per node for one cycle, read back from the ledger.

        From the ledger rather than from an in-memory tally, so the report is
        the same number the cap is enforced against. Two counters drift.
        """
        by_node: dict[str, NodeCost] = {}
        for entry in self._ledger.entries_for_cycle(cycle_id):
            running = by_node.get(entry.node)
            if running is None:
                by_node[entry.node] = NodeCost(
                    node=entry.node,
                    calls=1,
                    input_tokens=entry.input_tokens,
                    output_tokens=entry.output_tokens,
                    cost=entry.cost,
                )
                continue
            by_node[entry.node] = NodeCost(
                node=entry.node,
                calls=running.calls + 1,
                input_tokens=running.input_tokens + entry.input_tokens,
                output_tokens=running.output_tokens + entry.output_tokens,
                cost=running.cost + entry.cost,
            )

        nodes = sorted(by_node.values(), key=lambda item: item.cost.paise, reverse=True)
        total = Money.zero()
        for node in nodes:
            total = total + node.cost
        return CycleCostReport(cycle_id=cycle_id, nodes=nodes, total=total)

    def _record[T: BaseModel](
        self,
        *,
        node: str,
        cycle_id: str,
        tier: ModelTier,
        model: str,
        result: LlmResult[T],
        started_at: datetime,
    ) -> None:
        ended_at = self._clock.utc_now()
        usage = result.usage
        self._ledger.record(
            LedgerEntry(
                cycle_id=cycle_id,
                node=node,
                tier=tier,
                model=model,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cost=usage.cost,
                called_at=ended_at,
            )
        )
        if self._tracer is None:
            return
        self._tracer.record(
            TraceSpan(
                cycle_id=cycle_id,
                node=node,
                model=model,
                started_at=started_at,
                ended_at=ended_at,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cost=usage.cost,
                ok=True,
                detail=type(result.value).__name__,
            )
        )

    def _trace_failure(
        self, node: str, cycle_id: str, model: str, started_at: datetime, error: Exception
    ) -> None:
        if self._tracer is None:
            return
        ended_at = self._clock.utc_now()
        self._tracer.record(
            TraceSpan(
                cycle_id=cycle_id,
                node=node,
                model=model,
                started_at=started_at,
                ended_at=ended_at,
                input_tokens=0,
                output_tokens=0,
                cost=Money.zero(),
                ok=False,
                detail=f"{type(error).__name__}: {error}",
            )
        )
