"""The governor: the cap, the ledger and the per-node report.

`docs/10` step 3.1 is done when "the cap, set to Rs 1, blocks the next call".
`TestTheCapBlocksRatherThanAlerts` is that requirement, induced rather than
assumed - the call must not reach the client at all.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import BaseModel

from tests.fakes import FakeLlm, FakeLlmLedger, FakeTracer, FrozenClock
from trading_desk.application.llm_governor import CostGovernedLlm
from trading_desk.domain.llm_cost import (
    LlmPricing,
    ModelPrice,
    ModelTier,
    MonthlyCapExceededError,
)
from trading_desk.domain.values import Money
from trading_desk.ports.llm_ledger import LedgerEntry

NOW = datetime(2026, 10, 19, 6, 0, tzinfo=UTC)  # 11:30 IST


class Verdict(BaseModel):
    answer: str


def _pricing(cap_rupees: str) -> LlmPricing:
    return LlmPricing(
        tiers={
            ModelTier.TRIAGE: ModelPrice(
                model="claude-haiku-5-5",
                input_usd_per_million=Decimal("0.10"),
                output_usd_per_million=Decimal("0.50"),
            ),
            ModelTier.SYNTHESIS: ModelPrice(
                model="claude-opus-5",
                input_usd_per_million=Decimal("5.00"),
                output_usd_per_million=Decimal("25.00"),
            ),
        },
        usd_to_inr=Decimal("89"),
        monthly_cap=Money.from_rupees(Decimal(cap_rupees)),
        verified=False,
        source="test",
    )


def _governor(
    cap_rupees: str = "2000",
    client: FakeLlm | None = None,
    ledger: FakeLlmLedger | None = None,
    tracer: FakeTracer | None = None,
) -> CostGovernedLlm:
    return CostGovernedLlm(
        client=client or FakeLlm(),
        ledger=ledger or FakeLlmLedger(),
        pricing=_pricing(cap_rupees),
        clock=FrozenClock(NOW),
        tracer=tracer,
    )


class TestTheCapBlocksRatherThanAlerts:
    def test_a_one_rupee_cap_refuses_the_very_first_call(self) -> None:
        """The step's stated done-when. Nothing has been spent yet, so only a
        check that counts the *estimate* can refuse here."""
        client = FakeLlm()
        client.queue(Verdict(answer="never reached"))
        governor = _governor(cap_rupees="1", client=client)

        with pytest.raises(MonthlyCapExceededError):
            governor.complete(
                node="triage",
                cycle_id="c1",
                tier=ModelTier.SYNTHESIS,
                system_prompt="you are a classifier",
                user_content="x" * 5000,
                response_model=Verdict,
            )

        # Induced, not assumed: the model was never called.
        assert client.calls == []

    def test_nothing_is_written_to_the_ledger_for_a_refused_call(self) -> None:
        ledger = FakeLlmLedger()
        governor = _governor(cap_rupees="1", ledger=ledger)

        with pytest.raises(MonthlyCapExceededError):
            governor.complete(
                node="triage",
                cycle_id="c1",
                tier=ModelTier.SYNTHESIS,
                system_prompt="s",
                user_content="u" * 5000,
                response_model=Verdict,
            )

        assert ledger.entries == []

    def test_the_refusal_says_what_was_spent_and_what_the_cap_is(self) -> None:
        governor = _governor(cap_rupees="1")

        with pytest.raises(MonthlyCapExceededError) as caught:
            governor.complete(
                node="triage",
                cycle_id="c1",
                tier=ModelTier.SYNTHESIS,
                system_prompt="s",
                user_content="u" * 5000,
                response_model=Verdict,
            )

        verdict = caught.value.verdict
        assert verdict.cap == Money.from_rupees(Decimal("1"))
        assert verdict.spent == Money.zero()
        assert verdict.estimated.paise > 0

    def test_spend_already_on_the_ledger_counts_against_the_cap(self) -> None:
        """A cap that only counted this process would reset on every restart."""
        ledger = FakeLlmLedger()
        ledger.record(
            LedgerEntry(
                cycle_id="earlier",
                node="news",
                tier=ModelTier.SYNTHESIS,
                model="claude-opus-5",
                input_tokens=1,
                output_tokens=1,
                cost=Money.from_rupees(Decimal("1999")),
                called_at=NOW - timedelta(days=3),
            )
        )
        governor = _governor(cap_rupees="2000", ledger=ledger)

        with pytest.raises(MonthlyCapExceededError):
            governor.complete(
                node="synthesis",
                cycle_id="c1",
                tier=ModelTier.SYNTHESIS,
                system_prompt="s",
                user_content="u" * 20000,
                response_model=Verdict,
            )

    def test_last_months_spend_does_not_count_against_this_month(self) -> None:
        ledger = FakeLlmLedger()
        ledger.record(
            LedgerEntry(
                cycle_id="september",
                node="news",
                tier=ModelTier.SYNTHESIS,
                model="claude-opus-5",
                input_tokens=1,
                output_tokens=1,
                cost=Money.from_rupees(Decimal("5000")),
                called_at=NOW - timedelta(days=40),
            )
        )
        client = FakeLlm()
        client.queue(Verdict(answer="ok"))
        governor = _governor(cap_rupees="2000", client=client, ledger=ledger)

        result = governor.complete(
            node="triage",
            cycle_id="c1",
            tier=ModelTier.TRIAGE,
            system_prompt="s",
            user_content="u",
            response_model=Verdict,
        )

        assert result.value.answer == "ok"

    def test_budget_check_reports_without_spending_anything(self) -> None:
        """A cycle can ask up front whether it can afford to run, rather than
        failing halfway and leaving a part-written briefing."""
        client = FakeLlm()
        governor = _governor(cap_rupees="1", client=client)

        verdict = governor.budget_check(ModelTier.SYNTHESIS, "s", "u" * 5000)

        assert not verdict.allowed
        assert client.calls == []


class TestAnAllowedCall:
    def test_the_tier_chooses_the_model(self) -> None:
        client = FakeLlm()
        client.queue(Verdict(answer="ok"))
        governor = _governor(client=client)

        governor.complete(
            node="dedup",
            cycle_id="c1",
            tier=ModelTier.TRIAGE,
            system_prompt="s",
            user_content="u",
            response_model=Verdict,
        )

        assert client.calls[0][0] == "claude-haiku-5-5"

    def test_the_call_is_recorded_with_its_node_and_cycle(self) -> None:
        ledger = FakeLlmLedger()
        client = FakeLlm()
        client.queue(Verdict(answer="ok"))
        governor = _governor(client=client, ledger=ledger)

        governor.complete(
            node="entity_linker",
            cycle_id="cycle-7",
            tier=ModelTier.TRIAGE,
            system_prompt="s",
            user_content="u",
            response_model=Verdict,
        )

        assert len(ledger.entries) == 1
        assert ledger.entries[0].node == "entity_linker"
        assert ledger.entries[0].cycle_id == "cycle-7"
        assert ledger.entries[0].called_at == NOW


class TestTracing:
    def test_a_successful_call_is_traced(self) -> None:
        tracer = FakeTracer()
        client = FakeLlm()
        client.queue(Verdict(answer="ok"))
        governor = _governor(client=client, tracer=tracer)

        governor.complete(
            node="dedup",
            cycle_id="c1",
            tier=ModelTier.TRIAGE,
            system_prompt="s",
            user_content="u",
            response_model=Verdict,
        )

        assert len(tracer.spans) == 1
        assert tracer.spans[0].ok

    def test_a_failed_call_is_traced_too(self) -> None:
        """A trace holding only successes hides the runs worth reading."""
        tracer = FakeTracer()
        client = FakeLlm()  # nothing queued, so it raises
        governor = _governor(client=client, tracer=tracer)

        with pytest.raises(AssertionError):
            governor.complete(
                node="dedup",
                cycle_id="c1",
                tier=ModelTier.TRIAGE,
                system_prompt="s",
                user_content="u",
                response_model=Verdict,
            )

        assert len(tracer.spans) == 1
        assert not tracer.spans[0].ok
        assert tracer.spans[0].node == "dedup"

    def test_a_refused_call_raises_before_anything_is_traced(self) -> None:
        tracer = FakeTracer()
        governor = _governor(cap_rupees="1", tracer=tracer)

        with pytest.raises(MonthlyCapExceededError):
            governor.complete(
                node="dedup",
                cycle_id="c1",
                tier=ModelTier.SYNTHESIS,
                system_prompt="s",
                user_content="u" * 5000,
                response_model=Verdict,
            )

        assert tracer.spans == []


class TestTheCostReport:
    def test_cost_is_reported_per_node_and_in_total(self) -> None:
        ledger = FakeLlmLedger()
        client = FakeLlm()
        client.queue(Verdict(answer="a"), Verdict(answer="b"), Verdict(answer="c"))
        governor = _governor(client=client, ledger=ledger)

        for node in ("dedup", "dedup", "synthesis"):
            governor.complete(
                node=node,
                cycle_id="c1",
                tier=ModelTier.TRIAGE,
                system_prompt="s",
                user_content="u",
                response_model=Verdict,
            )

        report = governor.cost_report("c1")

        assert {node.node: node.calls for node in report.nodes} == {"dedup": 2, "synthesis": 1}
        assert report.total == Money.from_rupees(Decimal("1.50"))  # 3 x the fake's 50 paise

    def test_another_cycle_is_not_counted(self) -> None:
        ledger = FakeLlmLedger()
        client = FakeLlm()
        client.queue(Verdict(answer="a"), Verdict(answer="b"))
        governor = _governor(client=client, ledger=ledger)

        for cycle in ("c1", "c2"):
            governor.complete(
                node="dedup",
                cycle_id=cycle,
                tier=ModelTier.TRIAGE,
                system_prompt="s",
                user_content="u",
                response_model=Verdict,
            )

        assert governor.cost_report("c1").total == Money.from_rupees(Decimal("0.50"))

    def test_an_empty_cycle_reports_zero_rather_than_raising(self) -> None:
        report = _governor().cost_report("never-ran")

        assert report.nodes == []
        assert report.total == Money.zero()
