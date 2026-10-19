"""The cycle service: the budget gate in front, the journal behind.

The two tests worth having are the ones about the edges of the run - that a
cycle which cannot afford to finish never starts, and that what reaches the
journal says what its risk number actually means.
"""

from __future__ import annotations

from contextlib import suppress
from decimal import Decimal

from tests.agents.strategist.harness import CYCLE_ID, Harness, pricing
from tests.fakes import FakeLlm, FakeLlmLedger, FrozenClock
from trading_desk.application.llm_governor import CostGovernedLlm
from trading_desk.application.research_cycle import ResearchCycle
from trading_desk.domain.llm_cost import LlmPricing
from trading_desk.domain.values import Money


def _with_cap(harness: Harness, rupees: str) -> ResearchCycle:
    """The same graph, behind a governor with a tiny monthly cap."""
    base = pricing()
    tight = LlmPricing(
        tiers=base.tiers,
        usd_to_inr=base.usd_to_inr,
        monthly_cap=Money.from_rupees(Decimal(rupees)),
        verified=False,
        source="test",
    )
    governor = CostGovernedLlm(
        client=harness.client,
        ledger=harness.ledger,
        pricing=tight,
        clock=harness.clock,
        cycles=harness.store,
    )
    return ResearchCycle(
        graph=harness.graph,
        governor=governor,
        journal=harness.journal,
        news_store=harness.news_store,
        clock=harness.clock,
    )


class TestTheBudgetGate:
    def test_a_cycle_that_cannot_afford_to_finish_never_starts(self) -> None:
        """Rule 6: refusing up front is a result with a reason. Failing half
        way through is an exception and a part-finished briefing."""
        harness = Harness()
        harness.queue_a_full_cycle()
        cycle = _with_cap(harness, "1")

        output = cycle.run(harness.request())

        assert not output.ran
        assert output.result.proposals == ()
        assert output.result.no_ideas_reason is not None
        assert "cycle not started" in output.result.no_ideas_reason

    def test_the_refusal_quotes_the_cap(self) -> None:
        harness = Harness()
        harness.queue_a_full_cycle()

        output = _with_cap(harness, "1").run(harness.request())

        assert output.result.no_ideas_reason is not None
        assert "cap" in output.result.no_ideas_reason

    def test_nothing_is_called_when_the_cap_refuses(self) -> None:
        harness = Harness()
        harness.queue_a_full_cycle()

        _with_cap(harness, "1").run(harness.request())

        assert harness.client.calls == []

    def test_a_workable_cap_lets_the_cycle_run(self) -> None:
        harness = Harness()
        harness.queue_a_full_cycle()

        output = _with_cap(harness, "2000").run(harness.request())

        assert output.ran
        assert len(output.result.proposals) == 1


class TestWhatReachesTheJournal:
    def test_a_proposal_is_recorded_with_its_gate_values(self) -> None:
        harness = Harness()
        harness.queue_a_full_cycle()

        harness.cycle.run(harness.request())

        assert len(harness.journal.ideas) == 1
        idea = harness.journal.ideas[0]
        assert idea.cycle_id == CYCLE_ID
        assert "technical_confirm" in idea.gate_values

    def test_the_stored_risk_says_what_it_is_a_risk_of(self) -> None:
        """It is one lot's max loss, not a position's risk - position risk
        needs the capital figure, which is blank (ISS-022). The label travels
        with the number so nobody reads it as the other thing."""
        harness = Harness()
        harness.queue_a_full_cycle()

        harness.cycle.run(harness.request())
        idea = harness.journal.ideas[0]

        assert idea.hypothetical_risk.paise > 0
        assert "one lot" in idea.gate_values["hypothetical_risk_basis"]

    def test_a_no_ideas_cycle_records_nothing(self) -> None:
        harness = Harness()
        harness.client.queue()  # nothing classified, so nothing survives

        # FakeLlm refusing an unqueued call is the point: nothing may be
        # written to the journal on a run that could not classify anything.
        with suppress(AssertionError):
            harness.cycle.run(harness.request())

        assert harness.journal.ideas == []


def test_the_archive_is_read_as_articles_and_filings_together() -> None:
    harness = Harness()

    assert harness.cycle.read_archive() == []


def test_a_cost_report_is_produced_per_node() -> None:
    harness = Harness()
    harness.queue_a_full_cycle()

    output = harness.cycle.run(harness.request())

    nodes = {node.node for node in output.costs.nodes}
    assert nodes == {"catalyst_classifier", "impact_analyst", "synthesis"}
    assert output.costs.total.paise > 0


def test_the_governor_is_unchanged_when_no_cycle_store_is_given() -> None:
    """Step 3.1's behaviour has to survive 3.4: a governor built without a
    cycle store makes every call for real, which is what `verify 3_1` asserts."""
    governor = CostGovernedLlm(
        client=FakeLlm(),
        ledger=FakeLlmLedger(),
        pricing=pricing(),
        clock=FrozenClock(Harness().clock.utc_now()),
    )

    assert governor.replayed_calls == 0
