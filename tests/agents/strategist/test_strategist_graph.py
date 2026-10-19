"""One research cycle, end to end, against fakes.

The tests that earn their place are the ones about *not* producing an idea:
the no-ideas path, the two-proposal ceiling, the single-source rejection and
the invented-number drop. A graph that always finds something to say is the
failure mode `docs/02` warns about, and it looks exactly like a working graph
until the money is real.
"""

from __future__ import annotations

import pytest

from tests.agents.strategist.harness import (
    AS_OF,
    CYCLE_ID,
    Harness,
    catalyst_response,
    impact_response,
    synthesis_response,
    two_outlets,
    two_outlets_for,
)
from trading_desk.agents.news.catalyst_classifier import CatalystResponse
from trading_desk.agents.strategist.synthesis import SynthesisResponse
from trading_desk.domain.catalysts import CatalystType
from trading_desk.domain.cycle import CycleNode, NodeStatus
from trading_desk.domain.proposals import MAX_PROPOSALS_PER_CYCLE
from trading_desk.domain.ranking import UNCALIBRATED_CONFIDENCE_CEILING

EXPECTED_NODES = 6


class TestAFullCycle:
    def test_a_tradable_day_produces_one_ranked_proposal(self) -> None:
        harness = Harness()
        harness.queue_a_full_cycle()

        outcome = harness.graph.run(harness.request())

        assert len(outcome.result.proposals) == 1
        proposal = outcome.result.proposals[0]
        assert proposal.rank == 1
        assert proposal.instrument_symbol == "RELIANCE"
        assert outcome.result.no_ideas_reason is None

    def test_every_gate_value_travels_with_the_proposal(self) -> None:
        """Rule 9 and FR-4.10: the briefing shows its working, so the numbers
        have to survive the trip from the gate to the proposal."""
        harness = Harness()
        harness.queue_a_full_cycle()

        proposal = harness.graph.run(harness.request()).result.proposals[0]

        assert "not_already_priced" in proposal.gate_values
        assert "technical_confirm" in proposal.gate_values
        assert all(value for value in proposal.gate_values.values())

    def test_the_thesis_rests_on_two_independent_publishers(self) -> None:
        harness = Harness()
        harness.queue_a_full_cycle()

        thesis = harness.graph.run(harness.request()).result.proposals[0].thesis

        assert thesis.independent_sources() == {"WIRE A", "WIRE B"}

    def test_confidence_is_capped_because_nothing_has_calibrated_it(self) -> None:
        harness = Harness()
        harness.queue_a_full_cycle()

        thesis = harness.graph.run(harness.request()).result.proposals[0].thesis

        assert thesis.confidence <= UNCALIBRATED_CONFIDENCE_CEILING

    def test_the_levels_come_from_the_structure_not_from_a_multiplier(self) -> None:
        harness = Harness()
        harness.queue_a_full_cycle()

        proposal = harness.graph.run(harness.request()).result.proposals[0]

        strikes = {leg.strike.paise for leg in proposal.legs}
        assert proposal.stop.level.paise in strikes
        assert proposal.target.level.paise in strikes

    def test_all_six_nodes_are_recorded_started_then_completed(self) -> None:
        """The delegation tree, FR-4.3. STARTED first so a crash leaves
        evidence that the node was reached."""
        harness = Harness()
        harness.queue_a_full_cycle()

        harness.graph.run(harness.request())
        runs = harness.store.node_runs(str(CYCLE_ID))

        started = [run.node for run in runs if run.status is NodeStatus.STARTED]
        completed = [run.node for run in runs if run.status is NodeStatus.COMPLETED]
        assert started == [node.value for node in CycleNode]
        assert completed == started
        assert len(runs) == EXPECTED_NODES * 2

    def test_every_artifact_joins_on_the_cycle_id(self) -> None:
        """FR-4.1. One id, and everything written during the run carries it."""
        harness = Harness()
        harness.queue_a_full_cycle()

        outcome = harness.graph.run(harness.request())

        assert outcome.result.cycle_id == CYCLE_ID
        assert {run.cycle_id for run in harness.store.node_runs(str(CYCLE_ID))} == {str(CYCLE_ID)}
        assert {entry.cycle_id for entry in harness.ledger.entries_for_cycle(str(CYCLE_ID))} == {
            str(CYCLE_ID)
        }


class TestNoIdeasIsAResult:
    def test_a_day_with_no_catalyst_says_so(self) -> None:
        harness = Harness()
        harness.client.queue(
            CatalystResponse(catalyst_type=CatalystType.NONE, confidence=0.8, evidence_quote="")
        )

        outcome = harness.graph.run(harness.request())

        assert outcome.result.proposals == ()
        assert outcome.result.no_ideas_reason is not None
        assert "0 survived classification" in outcome.result.no_ideas_reason

    def test_a_rejected_candidate_names_the_gate_that_killed_it(self) -> None:
        """FR-4.10. The IV rank is pushed above Setup A's ceiling, so the
        volatility gate is the one that must appear."""
        harness = Harness()
        harness.queue_a_full_cycle()
        request = harness.request()
        panicked = {
            symbol: rank.__class__(
                value=95.0,
                source=rank.source,
                sample_count=rank.sample_count,
                window=rank.window,
            )
            for symbol, rank in request.iv_ranks.items()
        }

        outcome = harness.graph.run(
            request.__class__(
                cycle_id=request.cycle_id,
                as_of=request.as_of,
                universe=request.universe,
                articles=request.articles,
                iv_ranks=panicked,
            )
        )

        assert outcome.result.proposals == ()
        assert [item.killed_by for item in outcome.result.rejected] == ["iv_rank"]
        assert "95.0" in outcome.result.rejected[0].measured

    def test_the_no_ideas_reason_counts_the_killers(self) -> None:
        harness = Harness()
        harness.queue_a_full_cycle()
        request = harness.request()
        outcome = harness.graph.run(
            request.__class__(
                cycle_id=request.cycle_id,
                as_of=request.as_of,
                universe=request.universe,
                articles=request.articles,
                iv_ranks={},  # no rank supplied for anything
            )
        )

        assert outcome.result.no_ideas_reason is not None
        assert "iv_rank" in outcome.result.no_ideas_reason


class TestTheTwoProposalCeiling:
    def test_three_tradable_names_produce_two_proposals_and_one_rank_rejection(self) -> None:
        harness = Harness()
        harness.add_symbol("INFY")
        harness.add_symbol("TCS")
        articles = two_outlets() + two_outlets_for("INFY") + two_outlets_for("TCS")
        # Three stories: classify and assess each, then write only the top two.
        harness.client.queue(
            catalyst_response(),
            impact_response(),
            catalyst_response(),
            impact_response(),
            catalyst_response(),
            impact_response(),
            synthesis_response(),
            synthesis_response(),
        )

        outcome = harness.graph.run(harness.request(articles))

        assert len(outcome.result.proposals) == MAX_PROPOSALS_PER_CYCLE
        assert [item.killed_by for item in outcome.result.rejected] == ["rank"]
        assert {item.rank for item in outcome.result.proposals} == {1, 2}

    def test_the_cycle_pays_for_two_syntheses_not_three(self) -> None:
        """Ranking runs before synthesis, which is the whole reason it does."""
        harness = Harness()
        harness.add_symbol("INFY")
        harness.add_symbol("TCS")
        harness.client.queue(
            catalyst_response(),
            impact_response(),
            catalyst_response(),
            impact_response(),
            catalyst_response(),
            impact_response(),
            synthesis_response(),
            synthesis_response(),
        )

        harness.graph.run(
            harness.request(two_outlets() + two_outlets_for("INFY") + two_outlets_for("TCS"))
        )

        synthesis_calls = [
            entry
            for entry in harness.ledger.entries_for_cycle(str(CYCLE_ID))
            if entry.node == "synthesis"
        ]
        assert len(synthesis_calls) == MAX_PROPOSALS_PER_CYCLE


class TestTheModelCannotDecideAnything:
    def test_an_invented_number_drops_the_idea_and_says_which_number(self) -> None:
        harness = Harness()
        harness.queue_a_full_cycle(
            SynthesisResponse(
                claim="Profit is expected to rise 37% on the back of this order.",
                invalidation="The thesis fails if the stock closes back inside its range.",
                rationale="",
            )
        )

        outcome = harness.graph.run(harness.request())

        assert outcome.result.proposals == ()
        rejection = outcome.result.rejected[0]
        assert rejection.killed_by == "synthesis_grounding"
        assert "37" in rejection.measured

    def test_a_single_source_story_cannot_become_a_thesis(self) -> None:
        """FR-4.6, reached through the graph rather than asserted on the type.
        One publisher is the normal case for an exchange filing, so this path
        is expected to fire often on real data."""
        harness = Harness()
        harness.queue_a_full_cycle()

        outcome = harness.graph.run(harness.request(two_outlets()[:1]))

        assert outcome.result.proposals == ()
        assert outcome.result.rejected[0].killed_by == "independent_sources"

    def test_the_proposal_carries_no_quantity(self) -> None:
        harness = Harness()
        harness.queue_a_full_cycle()

        proposal = harness.graph.run(harness.request()).result.proposals[0]

        banned = {"quantity", "lots", "size", "order_price"}
        assert not banned & set(proposal.__dataclass_fields__)
        assert not banned & set(proposal.legs[0].__dataclass_fields__)


class TestResume:
    def test_a_second_run_of_the_same_cycle_makes_no_model_call(self) -> None:
        """The crash-resume guarantee, induced: the second graph's model has
        nothing queued, so any real call would raise. It does not raise,
        because every call is replayed from the cycle store."""
        first = Harness()
        first.queue_a_full_cycle()
        first.graph.run(first.request())

        resumed = Harness(store=first.store, ledger=first.ledger)
        # Nothing queued on purpose.
        resumed.graph.run(resumed.request())

        assert resumed.client.calls == []
        assert resumed.governor.replayed_calls == 3

    def test_the_resumed_cycle_reaches_the_same_answer(self) -> None:
        first = Harness()
        first.queue_a_full_cycle()
        before = first.graph.run(first.request())

        resumed = Harness(store=first.store, ledger=first.ledger)
        after = resumed.graph.run(resumed.request())

        assert before.result.proposals[0].proposal_id == after.result.proposals[0].proposal_id
        assert before.result.proposals[0].gate_values == after.result.proposals[0].gate_values
        assert before.result.proposals[0].thesis.claim == after.result.proposals[0].thesis.claim

    def test_the_resume_costs_nothing(self) -> None:
        """Three ledger rows after two runs, not six. The ledger is what the
        monthly cap is enforced against, so a double-charged resume would
        quietly shrink the budget."""
        first = Harness()
        first.queue_a_full_cycle()
        first.graph.run(first.request())
        spent_once = len(first.ledger.entries_for_cycle(str(CYCLE_ID)))

        resumed = Harness(store=first.store, ledger=first.ledger)
        resumed.graph.run(resumed.request())

        assert len(resumed.ledger.entries_for_cycle(str(CYCLE_ID))) == spent_once


class TestDeterminism:
    def test_three_runs_produce_identical_gate_values(self) -> None:
        """docs/12's determinism boundary. The prose is the model's and may
        differ; every gate value is arithmetic and may not."""
        runs = []
        for _ in range(3):
            harness = Harness()
            harness.queue_a_full_cycle()
            runs.append(harness.graph.run(harness.request()).result.proposals[0].gate_values)

        assert runs[0] == runs[1] == runs[2]

    def test_two_identical_candidates_always_rank_the_same_way_round(self) -> None:
        """The tie-break. Without it this test flaps and nothing else does."""
        orders = []
        for _ in range(3):
            harness = Harness()
            harness.add_symbol("INFY")
            harness.client.queue(
                catalyst_response(),
                impact_response(),
                catalyst_response(),
                impact_response(),
                synthesis_response(),
                synthesis_response(),
            )
            outcome = harness.graph.run(harness.request(two_outlets() + two_outlets_for("INFY")))
            orders.append([item.instrument_symbol for item in outcome.result.ranked()])

        assert orders[0] == orders[1] == orders[2]
        assert orders[0] == ["INFY", "RELIANCE"]


def test_a_failing_node_is_recorded_as_failed_and_the_cycle_stops() -> None:
    """Rule 6: a specialist that raises must not produce a partial briefing."""
    harness = Harness()
    harness.queue_a_full_cycle()
    harness.chains.fail_next_fetch = True

    with pytest.raises(ConnectionError):
        harness.graph.run(harness.request())

    runs = harness.store.node_runs(str(CYCLE_ID))
    failed = [run for run in runs if run.status is NodeStatus.FAILED]
    assert [run.node for run in failed] == [CycleNode.OPTIONS.value]


def test_the_missing_inputs_are_reported_every_run() -> None:
    """Two of Setup A's calendar inputs have no source and both default to the
    permissive answer (ISS-028). Silence about that would read as a clean run."""
    harness = Harness()
    harness.queue_a_full_cycle()

    outcome = harness.graph.run(harness.request())

    assert any("ban list" in item for item in outcome.missing_inputs)
    assert any("earnings calendar" in item for item in outcome.missing_inputs)
    assert outcome.result.generated_at == AS_OF
