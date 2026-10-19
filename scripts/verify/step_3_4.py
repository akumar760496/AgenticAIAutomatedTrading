"""Verify step 3.4 - the Chief Strategist graph.

`docs/10`: done when "a full cycle runs against fakes end to end; a killed
process resumes without repeating finished nodes; every artifact joins on
`cycle_id`."

`docs/12` adds the sharpest check in the project, and it is the one this
script is built around:

> "Determinism boundary: run the same cycle on the same inputs three times.
> The *prose* will differ; the **gate values must be identical**, because
> gates are arithmetic. Differing gate values mean an LLM has leaked into the
> decision path, which is the most important invariant in the system."

So it is checked twice here, two different ways: three identical runs, and -
the stronger form - one run where the model is made to write **different
prose** about the same facts. If a gate value moves when only the wording
changed, the model has reached something it should not have.

Resume is checked by induction, not by assertion: the second graph's model has
nothing queued, so any real call raises. It does not raise, and the ledger is
unchanged, which is what "repeats no paid work" means.

The two database drills run inside a transaction that is rolled back (ISS-024:
an in-place drill against an append-only table leaves a row that cannot be
deleted by design).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

from pydantic import ValidationError
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError
from tests.agents.strategist.harness import (
    CYCLE_ID,
    Harness,
    catalyst_response,
    impact_response,
    pricing,
    synthesis_response,
    two_outlets,
    two_outlets_for,
)
from tests.fakes import FakeLlmLedger

from scripts.verify._runner import CheckResult
from trading_desk.agents.strategist.graph import CycleOutcome
from trading_desk.agents.strategist.synthesis import SynthesisResponse
from trading_desk.application.llm_governor import CostGovernedLlm
from trading_desk.application.research_cycle import ResearchCycle
from trading_desk.domain.cycle import CycleNode, NodeStatus
from trading_desk.domain.llm_cost import LlmPricing
from trading_desk.domain.proposals import MAX_PROPOSALS_PER_CYCLE, ResearchCycleResult
from trading_desk.domain.values import Money
from trading_desk.infra.config import load_infra_settings
from trading_desk.infra.db import create_db_engine

TITLE = "The Chief Strategist graph"

EXPECTED_NODES = 6
EXPECTED_PAID_CALLS = 3  # classify, impact, synthesise
DETERMINISM_RUNS = 3
# Two inputs have no data source and both default to the permissive answer.
EXPECTED_MISSING_INPUTS = 2
BANNED_FIELDS = {"quantity", "lots", "lot_count", "size", "order_price", "order_type"}
NOW = datetime(2026, 10, 19, 4, 0, tzinfo=UTC)


def _run_once() -> CycleOutcome:
    harness = Harness()
    harness.queue_a_full_cycle()
    return harness.graph.run(harness.request())


def _three_names(harness: Harness) -> CycleOutcome:
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
    articles = two_outlets() + two_outlets_for("INFY") + two_outlets_for("TCS")
    return harness.graph.run(harness.request(articles))


# --------------------------------------------------------------------------
# The cycle itself
# --------------------------------------------------------------------------


def _a_full_cycle_runs_end_to_end() -> tuple[bool, str]:
    outcome = _run_once()
    gates = sum(len(report.results) for report in outcome.gate_reports)
    return len(outcome.result.proposals) == 1, (
        f"{outcome.news.articles_in} articles -> {outcome.news.stories_after_dedup} stories "
        f"-> {len(outcome.gate_reports)} candidate through {gates} gates "
        f"-> {len(outcome.result.proposals)} proposal"
    )


def _no_ideas_is_a_first_class_result() -> tuple[bool, str]:
    """FR-4.9. An empty list with no reason looks exactly like a crash."""
    harness = Harness()
    harness.queue_a_full_cycle()
    request = harness.request()
    outcome = harness.graph.run(replace(request, iv_ranks={}))

    reason = outcome.result.no_ideas_reason
    return bool(reason) and not outcome.result.proposals, (
        f'no proposals, reason given: "{reason}"' if reason else "no proposals and NO reason"
    )


def _at_most_two_proposals() -> tuple[bool, str]:
    """FR-4.8, both ways: the graph caps it, and the type refuses a third."""
    outcome = _three_names(Harness())
    capped = len(outcome.result.proposals) == MAX_PROPOSALS_PER_CYCLE

    try:
        ResearchCycleResult(
            cycle_id=CYCLE_ID,
            generated_at=NOW,
            proposals=(*outcome.result.proposals, outcome.result.proposals[0]),
        )
    except ValueError:
        type_refuses = True
    else:
        type_refuses = False

    overflow = [item.killed_by for item in outcome.result.rejected]
    return capped and type_refuses, (
        f"3 tradable names -> {len(outcome.result.proposals)} proposals, "
        f"overflow rejected as {overflow}; a third is refused by the type too"
    )


def _every_rejection_names_its_gate() -> tuple[bool, str]:
    """FR-4.10. A rejection that says only 'rejected' is unactionable."""
    harness = Harness()
    harness.queue_a_full_cycle()
    request = harness.request()
    panicked = {symbol: replace(rank, value=95.0) for symbol, rank in request.iv_ranks.items()}
    outcome = harness.graph.run(replace(request, iv_ranks=panicked))

    rejected = outcome.result.rejected
    complete = bool(rejected) and all(
        item.killed_by and item.measured and item.threshold for item in rejected
    )
    named = "; ".join(f"{item.killed_by}={item.measured}" for item in rejected)
    return complete, f"{len(rejected)} rejection(s), each with a gate and a value: {named}"


def _every_artifact_joins_on_the_cycle_id() -> tuple[bool, str]:
    """FR-4.1, checked across all four places a cycle writes."""
    harness = Harness()
    harness.queue_a_full_cycle()
    output = harness.cycle.run(harness.request())

    cycle = str(CYCLE_ID)
    ids = {
        "result": {str(output.result.cycle_id)},
        "node runs": {run.cycle_id for run in harness.store.node_runs(cycle)},
        "ledger": {entry.cycle_id for entry in harness.ledger.entries_for_cycle(cycle)},
        "journal": {str(idea.cycle_id) for idea in harness.journal.ideas},
    }
    wrong = {name: found for name, found in ids.items() if found != {cycle}}
    return not wrong, (
        f"result, node runs, ledger and journal all key on {cycle[:8]}…"
        if not wrong
        else f"mismatched: {wrong}"
    )


def _the_delegation_tree_is_recorded() -> tuple[bool, str]:
    """FR-4.3. STARTED before the work, so a crash leaves evidence."""
    harness = Harness()
    harness.queue_a_full_cycle()
    harness.graph.run(harness.request())
    runs = harness.store.node_runs(str(CYCLE_ID))

    started = [run.node for run in runs if run.status is NodeStatus.STARTED]
    completed = [run.node for run in runs if run.status is NodeStatus.COMPLETED]
    expected = [node.value for node in CycleNode]
    return started == expected and completed == expected, (
        f"{len(runs)} rows: {EXPECTED_NODES} nodes, each STARTED then COMPLETED "
        f"({' -> '.join(expected)})"
    )


# --------------------------------------------------------------------------
# The invariants
# --------------------------------------------------------------------------


def _gate_values_identical_across_three_runs() -> tuple[bool, str]:
    runs = [_run_once().result.proposals[0].gate_values for _ in range(DETERMINISM_RUNS)]
    identical = all(run == runs[0] for run in runs)
    return identical, (
        f"{DETERMINISM_RUNS} runs x {len(runs[0])} gate values, all identical"
        if identical
        else "gate values moved between runs"
    )


def _different_prose_does_not_move_a_gate_value() -> tuple[bool, str]:
    """The stronger form of the determinism check.

    Three identical runs prove the code is deterministic. This proves the
    *model* cannot reach the decision path: same facts, deliberately different
    wording, and every gate value has to be unchanged.
    """
    baseline = _run_once().result.proposals[0]

    harness = Harness()
    harness.queue_a_full_cycle(
        SynthesisResponse(
            claim="Momentum should carry the stock higher after a sizeable contract win.",
            invalidation="Wrong if the price slips back below the prior range.",
            rationale="Entirely different wording, same underlying facts.",
        )
    )
    reworded = harness.graph.run(harness.request()).result.proposals[0]

    same_gates = baseline.gate_values == reworded.gate_values
    prose_differs = baseline.thesis.claim != reworded.thesis.claim
    return same_gates and prose_differs, (
        f"{len(baseline.gate_values)} gate values unchanged while the claim changed"
        if same_gates and prose_differs
        else f"gates identical={same_gates}, prose differs={prose_differs}"
    )


def _a_crash_resume_repeats_no_paid_work() -> tuple[bool, str]:
    """FR-4.12, induced: the resumed graph's model has nothing queued."""
    first = Harness()
    first.queue_a_full_cycle()
    before = first.graph.run(first.request())
    paid = len(first.ledger.entries_for_cycle(str(CYCLE_ID)))

    resumed = Harness(store=first.store, ledger=first.ledger)
    after = resumed.graph.run(resumed.request())

    free = resumed.client.calls == []
    same = (
        before.result.proposals[0].gate_values == after.result.proposals[0].gate_values
        and before.result.proposals[0].proposal_id == after.result.proposals[0].proposal_id
    )
    still = len(resumed.ledger.entries_for_cycle(str(CYCLE_ID)))
    return free and same and still == paid, (
        f"{paid} paid calls on the first run, {resumed.governor.replayed_calls} replayed on "
        f"the second, {still} ledger rows in total, identical answer"
    )


def _a_malformed_response_fails_the_cycle() -> tuple[bool, str]:
    """Two shapes of malformed, because they fail in different places."""
    failures = []

    try:
        SynthesisResponse(claim="", invalidation="something observable")
    except ValidationError:
        pass
    else:
        failures.append("an empty claim validated")

    harness = Harness()
    harness.queue_a_full_cycle()
    harness.chains.fail_next_fetch = True
    try:
        harness.graph.run(harness.request())
    except ConnectionError:
        runs = harness.store.node_runs(str(CYCLE_ID))
        if not [run for run in runs if run.status is NodeStatus.FAILED]:
            failures.append("the failing node was not recorded as FAILED")
    else:
        failures.append("a failing chain fetch produced a result instead of raising")

    return not failures, (
        "2 of 2 malformed cases fail the cycle, and the failed node is recorded"
        if not failures
        else "; ".join(failures)
    )


def _an_invented_number_drops_the_idea() -> tuple[bool, str]:
    harness = Harness()
    harness.queue_a_full_cycle(
        SynthesisResponse(
            claim="Profit is expected to rise 37% on this order.",
            invalidation="Wrong if the stock closes back inside its range.",
        )
    )
    outcome = harness.graph.run(harness.request())

    rejected = outcome.result.rejected
    caught = (
        not outcome.result.proposals
        and bool(rejected)
        and rejected[0].killed_by == "synthesis_grounding"
    )
    return caught, (
        f"a figure that was not in the facts dropped the idea: {rejected[0].measured}"
        if caught
        else "an invented number reached the briefing"
    )


def _a_single_source_cannot_become_a_thesis() -> tuple[bool, str]:
    """FR-4.6 reached through the graph. On today's archive - exchange filings
    nobody has written about yet - this is the common case, not the edge one."""
    harness = Harness()
    harness.queue_a_full_cycle()
    outcome = harness.graph.run(harness.request(two_outlets()[:1]))

    rejected = outcome.result.rejected
    caught = (
        not outcome.result.proposals
        and rejected
        and rejected[0].killed_by == ("independent_sources")
    )
    return bool(caught), (
        "one publisher -> rejected, not quietly accepted as a one-source idea"
        if caught
        else "a single-source story became a proposal"
    )


def _the_cap_refuses_before_the_cycle_starts() -> tuple[bool, str]:
    """Rule 6: a cycle that cannot afford to finish must not begin."""
    harness = Harness()
    harness.queue_a_full_cycle()
    base = pricing()
    governor = CostGovernedLlm(
        client=harness.client,
        ledger=FakeLlmLedger(),
        pricing=LlmPricing(
            tiers=base.tiers,
            usd_to_inr=base.usd_to_inr,
            monthly_cap=Money.from_rupees(Decimal("1")),
            verified=False,
            source="verify",
        ),
        clock=harness.clock,
        cycles=harness.store,
    )
    cycle = ResearchCycle(
        graph=harness.graph,
        governor=governor,
        journal=harness.journal,
        news_store=harness.news_store,
        clock=harness.clock,
    )
    output = cycle.run(harness.request())

    refused = not output.ran and harness.client.calls == []
    return refused, (
        f"₹1 cap: cycle not started, 0 calls made, reason recorded "
        f'("{(output.result.no_ideas_reason or "")[:60]}…")'
        if refused
        else "the cycle ran anyway"
    )


def _no_proposal_can_express_a_quantity() -> tuple[bool, str]:
    """Rule 1, on everything a proposal reaches."""
    proposal = _run_once().result.proposals[0]
    found = [
        f"{type(obj).__name__}.{name}"
        for obj in (proposal, proposal.legs[0], proposal.stop, proposal.target, proposal.thesis)
        for name in obj.__dataclass_fields__
        if name.lower() in BANNED_FIELDS
    ]
    return not found, (
        "proposal, legs, stop, target and thesis: no size field anywhere"
        if not found
        else f"found {found}"
    )


def _the_missing_calendar_inputs_are_reported() -> tuple[bool, str]:
    """ISS-028. Both default to the permissive answer, so silence about them
    would read as a clean run."""
    outcome = _run_once()
    return len(outcome.missing_inputs) >= EXPECTED_MISSING_INPUTS, (
        f"{len(outcome.missing_inputs)} inputs reported missing every run: "
        + "; ".join(item.split(":")[0] for item in outcome.missing_inputs)
    )


# --------------------------------------------------------------------------
# The checkpoint tables
# --------------------------------------------------------------------------


def _engine() -> Engine:
    return create_db_engine(load_infra_settings())


def _attempt(table: str, statement: str) -> tuple[bool, str]:
    """Run one destructive statement inside a transaction that is rolled back.

    ISS-024: an in-place drill against an append-only table leaves a row that
    cannot be deleted, by design. Every write here is undone.
    """
    connection = _engine().connect()
    transaction = connection.begin()
    try:
        connection.execute(
            text(
                f"INSERT INTO {table} "
                + (
                    "(cycle_id, node, status, detail, measurements, recorded_at) VALUES "
                    "('drill', 'news', 'STARTED', '', '{}', now())"
                    if table == "cycle_nodes"
                    else "(cycle_id, node, call_key, model, payload, input_tokens, "
                    "output_tokens) VALUES ('drill', 'news', 'k', 'm', '{}', 1, 1)"
                )
            )
        )
        try:
            connection.execute(text(statement))
        except DBAPIError as error:
            if "append-only" in str(error):
                return True, f"{statement.split(maxsplit=1)[0]} rejected by the trigger"
            return False, f"refused, but not by the trigger: {str(error).splitlines()[0][:70]}"
        return False, f"{statement.split(maxsplit=1)[0]} was allowed on {table}"
    finally:
        transaction.rollback()
        connection.close()


def _the_checkpoint_tables_are_append_only() -> tuple[bool, str]:
    try:
        outcomes = [
            _attempt("cycle_nodes", "UPDATE cycle_nodes SET status = 'COMPLETED'"),
            _attempt("cycle_nodes", "DELETE FROM cycle_nodes"),
            _attempt("cycle_nodes", "TRUNCATE cycle_nodes"),
            _attempt("cycle_responses", "UPDATE cycle_responses SET payload = '{}'"),
            _attempt("cycle_responses", "DELETE FROM cycle_responses"),
            _attempt("cycle_responses", "TRUNCATE cycle_responses"),
        ]
    except Exception as error:
        return False, f"database unreachable: {type(error).__name__}"

    failures = [detail for passed, detail in outcomes if not passed]
    return not failures, (
        "UPDATE, DELETE and TRUNCATE all rejected on both tables (6 of 6), every drill rolled back"
        if not failures
        else "; ".join(failures)
    )


def _the_drill_left_nothing_behind() -> tuple[bool, str]:
    """ISS-024's actual lesson: prove the rollback worked."""
    try:
        with _engine().connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT (SELECT count(*) FROM cycle_nodes WHERE cycle_id = 'drill'), "
                    "(SELECT count(*) FROM cycle_responses WHERE cycle_id = 'drill')"
                )
            ).one()
    except Exception as error:
        return False, f"database unreachable: {type(error).__name__}"

    left = int(rows[0]) + int(rows[1])
    return left == 0, f"{left} drill rows remain in the checkpoint tables"


def run() -> list[CheckResult]:
    checks = [
        (
            "a_full_cycle_runs_end_to_end",
            _a_full_cycle_runs_end_to_end,
            "need one cycle, fakes only, no network",
        ),
        (
            "no_ideas_is_a_first_class_result",
            _no_ideas_is_a_first_class_result,
            "need a stated reason, never a bare empty list (FR-4.9)",
        ),
        (
            "at_most_two_proposals",
            _at_most_two_proposals,
            "need <= 2 from the graph and a refusal from the type (FR-4.8)",
        ),
        (
            "every_rejection_names_its_gate",
            _every_rejection_names_its_gate,
            "need a gate, a measured value and a threshold (FR-4.10)",
        ),
        (
            "every_artifact_joins_on_the_cycle_id",
            _every_artifact_joins_on_the_cycle_id,
            "need one id across result, nodes, ledger and journal (FR-4.1)",
        ),
        (
            "the_delegation_tree_is_recorded",
            _the_delegation_tree_is_recorded,
            f"need {EXPECTED_NODES} nodes, STARTED then COMPLETED (FR-4.3)",
        ),
        (
            "gate_values_identical_across_three_runs",
            _gate_values_identical_across_three_runs,
            "need byte-identical gate values (docs/12)",
        ),
        (
            "different_prose_does_not_move_a_gate_value",
            _different_prose_does_not_move_a_gate_value,
            "need the model unable to reach a decision (docs/12)",
        ),
        (
            "a_crash_resume_repeats_no_paid_work",
            _a_crash_resume_repeats_no_paid_work,
            "need 0 new calls and an identical answer (FR-4.12)",
        ),
        (
            "a_malformed_response_fails_the_cycle",
            _a_malformed_response_fails_the_cycle,
            "need a raise, never a partial briefing (rule 6)",
        ),
        (
            "an_invented_number_drops_the_idea",
            _an_invented_number_drops_the_idea,
            "need the figure caught and named",
        ),
        (
            "a_single_source_cannot_become_a_thesis",
            _a_single_source_cannot_become_a_thesis,
            "need 2 independent sources (FR-4.6)",
        ),
        (
            "the_cap_refuses_before_the_cycle_starts",
            _the_cap_refuses_before_the_cycle_starts,
            "need 0 calls when the month's budget cannot cover the cycle",
        ),
        (
            "no_proposal_can_express_a_quantity",
            _no_proposal_can_express_a_quantity,
            "need 0 size fields anywhere reachable (rule 1)",
        ),
        (
            "the_missing_calendar_inputs_are_reported",
            _the_missing_calendar_inputs_are_reported,
            "need both permissive defaults declared every run (ISS-028)",
        ),
        (
            "the_checkpoint_tables_are_append_only",
            _the_checkpoint_tables_are_append_only,
            "need UPDATE, DELETE and TRUNCATE all refused",
        ),
        (
            "the_drill_left_nothing_behind",
            _the_drill_left_nothing_behind,
            "need 0 drill rows - every write rolled back (ISS-024)",
        ),
    ]
    results = []
    for name, check, requirement in checks:
        passed, measured = check()
        results.append(
            CheckResult(name=name, passed=passed, measured=measured, requirement=requirement)
        )
    return results
