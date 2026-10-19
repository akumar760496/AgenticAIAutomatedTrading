"""Verify step 3.1 - the LLM adapter and cost governor.

`docs/10`: done when "a test run reports cost per node; the cap, set to Rs 1,
blocks the next call."

`docs/12` adds a third: the recorded-response fake works offline, so the suite
costs nothing.

The cap check here is **induced, not asserted**. It is not enough that a
refusal was raised - the client must never have been reached, because a refusal
raised after the call has already been paid for is an alert with better
manners. The fake records every call it receives, so an empty call list is the
evidence.

The last check is the one that will fail today and should: the rates in
`config/llm_pricing.yaml` are Anthropic's published numbers, but nobody has yet
compared a month of our ledger against the Anthropic console. Published rates
show the prices are right; only the console shows our *accounting* of them is.
"""

from __future__ import annotations

import contextlib
import inspect
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from pydantic import BaseModel
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError
from tests.fakes import FakeLlm, FakeLlmLedger, FakeTracer, FrozenClock

from scripts.verify._runner import CheckResult
from trading_desk.adapters.llm.recorded import CassetteMissError, RecordedLlm
from trading_desk.adapters.store.llm_ledger import SELECT_MONTH_SPEND
from trading_desk.application.llm_governor import CostGovernedLlm
from trading_desk.domain.llm_cost import (
    LlmPricing,
    ModelTier,
    MonthlyCapExceededError,
    cost_of_call,
)
from trading_desk.domain.values import Money
from trading_desk.infra.config import load_infra_settings
from trading_desk.infra.db import create_db_engine
from trading_desk.infra.llm_pricing import load_llm_pricing
from trading_desk.ports.llm_ledger import LedgerEntry

TITLE = "LLM adapter and cost governor"

NOW = datetime(2026, 10, 19, 6, 0, tzinfo=UTC)
PRICING_PATH = Path("config/llm_pricing.yaml")
ONE_RUPEE = Money.from_rupees(Decimal("1"))

# Three distinct nodes are driven through the cost report below.
EXPECTED_REPORT_NODES = 3
# Below this, two tiers are not worth the extra moving part.
MIN_TIERING_SAVING = 10

# A prompt big enough that even the triage tier costs more than a rupee, so the
# cap check is not accidentally passing because the call was free.
BIG_PROMPT = "x" * 200_000


class Verdict(BaseModel):
    answer: str


def _pricing(cap: Money | None = None) -> LlmPricing:
    loaded = load_llm_pricing(PRICING_PATH)
    if cap is None:
        return loaded
    return LlmPricing(
        tiers=loaded.tiers,
        usd_to_inr=loaded.usd_to_inr,
        monthly_cap=cap,
        verified=loaded.verified,
        source=loaded.source,
    )


def _governor(
    cap: Money | None = None,
    client: FakeLlm | None = None,
    ledger: FakeLlmLedger | None = None,
    tracer: FakeTracer | None = None,
) -> CostGovernedLlm:
    return CostGovernedLlm(
        client=client or FakeLlm(),
        ledger=ledger or FakeLlmLedger(),
        pricing=_pricing(cap),
        clock=FrozenClock(NOW),
        tracer=tracer,
    )


def _a_one_rupee_cap_blocks_the_call() -> tuple[bool, str]:
    """The step's stated done-when, induced."""
    client = FakeLlm()
    client.queue(Verdict(answer="must never be reached"))
    governor = _governor(cap=ONE_RUPEE, client=client)

    try:
        governor.complete(
            node="synthesis",
            cycle_id="verify",
            tier=ModelTier.SYNTHESIS,
            system_prompt="you are an analyst",
            user_content=BIG_PROMPT,
            response_model=Verdict,
        )
    except MonthlyCapExceededError as refusal:
        reached = len(client.calls)
        return reached == 0, (
            f"refused at a {refusal.verdict.cap} cap, estimated "
            f"{refusal.verdict.estimated}; client reached {reached} times"
        )
    return False, "the call went through - a 1 rupee cap did not block it"


def _the_cap_refuses_before_spending_not_after() -> tuple[bool, str]:
    """Spend sits just under the cap; the estimate is what pushes it over.

    A check that only compared spend against the cap would allow this call,
    and that call is unbounded in size.
    """
    ledger = FakeLlmLedger()
    ledger.record(
        LedgerEntry(
            cycle_id="earlier",
            node="news",
            tier=ModelTier.TRIAGE,
            model="claude-haiku-5-5",
            input_tokens=1,
            output_tokens=1,
            cost=Money.from_rupees(Decimal("1999")),
            called_at=NOW,
        )
    )
    client = FakeLlm()
    client.queue(Verdict(answer="must never be reached"))
    governor = _governor(cap=Money.from_rupees(Decimal("2000")), client=client, ledger=ledger)

    try:
        governor.complete(
            node="synthesis",
            cycle_id="verify",
            tier=ModelTier.SYNTHESIS,
            system_prompt="s",
            user_content=BIG_PROMPT,
            response_model=Verdict,
        )
    except MonthlyCapExceededError as refusal:
        return len(client.calls) == 0, (
            f"{refusal.verdict.spent} spent of {refusal.verdict.cap}, "
            f"headroom {refusal.verdict.headroom}, call estimated "
            f"{refusal.verdict.estimated} - refused"
        )
    return False, "under-cap spend plus an over-cap estimate was allowed through"


def _nothing_is_billed_for_a_refused_call() -> tuple[bool, str]:
    ledger = FakeLlmLedger()
    governor = _governor(cap=ONE_RUPEE, ledger=ledger)
    with contextlib.suppress(MonthlyCapExceededError):
        governor.complete(
            node="synthesis",
            cycle_id="verify",
            tier=ModelTier.SYNTHESIS,
            system_prompt="s",
            user_content=BIG_PROMPT,
            response_model=Verdict,
        )
    return not ledger.entries, f"{len(ledger.entries)} ledger rows after a refusal"


def _cost_is_reported_per_node() -> tuple[bool, str]:
    """The other half of the done-when: a run reports cost per node, not a total."""
    client = FakeLlm()
    client.queue(*[Verdict(answer=str(index)) for index in range(4)])
    governor = _governor(client=client)

    for node in ("deduper", "deduper", "classifier", "synthesis"):
        governor.complete(
            node=node,
            cycle_id="verify-cycle",
            tier=ModelTier.TRIAGE,
            system_prompt="s",
            user_content="u",
            response_model=Verdict,
        )

    report = governor.cost_report("verify-cycle")
    # Call counts, not rupees. The amounts here would come from FakeLlm's flat
    # 50 paise per call, which is a fixture constant, not a measurement - and
    # printing it beside the genuinely priced figures below is exactly how a
    # made-up number gets read as a real one.
    breakdown = ", ".join(f"{node.node}x{node.calls}" for node in report.nodes)
    return len(report.nodes) == EXPECTED_REPORT_NODES and not report.total.is_zero(), (
        f"grouped into {breakdown} (fake costs - this checks grouping, not pricing)"
    )


def _the_month_boundary_is_respected() -> tuple[bool, str]:
    """Last month's spend must not block this month's calls."""
    ledger = FakeLlmLedger()
    ledger.record(
        LedgerEntry(
            cycle_id="september",
            node="news",
            tier=ModelTier.TRIAGE,
            model="claude-haiku-5-5",
            input_tokens=1,
            output_tokens=1,
            cost=Money.from_rupees(Decimal("99999")),
            called_at=datetime(2026, 9, 15, 6, 0, tzinfo=UTC),
        )
    )
    governor = _governor(cap=Money.from_rupees(Decimal("2000")), ledger=ledger)
    spent = governor.spend_this_month()
    return spent.is_zero(), f"September held {Money.from_rupees(Decimal('99999'))}, October {spent}"


def _the_recorded_client_never_reaches_the_network() -> tuple[bool, str]:
    """A replay that falls through to a live model turns a free test run into
    a bill, and the invoice is the first you hear of it."""
    client = RecordedLlm(_pricing(), directory=Path("tests/cassettes/llm"))
    try:
        client.complete(
            system_prompt="nothing recorded",
            user_content="nothing recorded",
            response_model=Verdict,
            model="claude-haiku-5-5",
        )
    except CassetteMissError:
        return True, "a cassette miss raises; no fallback path to a live model exists"
    return False, "a missing cassette did not raise - it may have called out"


def _tiering_is_worth_its_complexity() -> tuple[bool, str]:
    """Measured, not assumed. If triage ever stops being much cheaper, the two
    tiers are costing complexity for nothing."""
    pricing = _pricing()
    triage = cost_of_call(pricing, ModelTier.TRIAGE, 1_000_000, 100_000)
    synthesis = cost_of_call(pricing, ModelTier.SYNTHESIS, 1_000_000, 100_000)
    ratio = Decimal(synthesis.paise) / Decimal(triage.paise)
    return ratio >= MIN_TIERING_SAVING, (
        f"1M in + 100k out: triage {triage} on {pricing.model_for(ModelTier.TRIAGE)}, "
        f"synthesis {synthesis} on {pricing.model_for(ModelTier.SYNTHESIS)} - {ratio:.0f}x"
    )


def _the_governor_is_the_only_way_in() -> tuple[bool, str]:
    """Every governed call must name its node and cycle, or the per-node report
    cannot be built and a briefing cannot say what it cost."""
    parameters = inspect.signature(CostGovernedLlm.complete).parameters
    required = {"node", "cycle_id", "tier"}
    missing = required - set(parameters)
    return not missing, (
        f"complete({', '.join(name for name in parameters if name != 'self')})"
        if not missing
        else f"missing {sorted(missing)}"
    )


def _rates_reconciled_against_the_console() -> tuple[bool, str]:
    """Fails by design until Ajay has compared one month against Anthropic."""
    pricing = _pricing()
    return pricing.verified, (
        f"verified={pricing.verified}, cap {pricing.monthly_cap}, "
        f"FX {pricing.usd_to_inr} INR/USD, source: {pricing.source}"
    )


# --------------------------------------------------------------------------
# The ledger in Postgres.
#
# Every one of these runs inside a transaction that is rolled back. The ledger
# is append-only by design, so a row written by a verification run could never
# be removed - and it would count against the month's cap forever. Found the
# hard way: the first trigger drill left a 134-paise row that had to be cleared
# by dropping and re-creating the table.
# --------------------------------------------------------------------------

DRILL_COST_PAISE = 134

INSERT_DRILL_ROW = text(
    """
    INSERT INTO llm_calls (
        cycle_id, node, tier, model, input_tokens, output_tokens, cost_paise, called_at
    ) VALUES (
        'verify-3-1', 'drill', 'triage', 'claude-haiku-5-5', 1000, 200, 134, :called_at
    )
    """
)


def _engine() -> Engine:
    return create_db_engine(load_infra_settings())


def _ledger_round_trips_through_postgres() -> tuple[bool, str]:
    """The month sum the cap reads must come back as the paise that went in."""
    connection = _engine().connect()
    transaction = connection.begin()
    try:
        connection.execute(INSERT_DRILL_ROW, {"called_at": NOW})
        total = connection.execute(SELECT_MONTH_SPEND, {"year": 2026, "month": 10}).scalar_one()
        other_month = connection.execute(
            SELECT_MONTH_SPEND, {"year": 2026, "month": 9}
        ).scalar_one()
    finally:
        transaction.rollback()
        connection.close()

    return int(total) == DRILL_COST_PAISE and int(other_month) == 0, (
        f"October sums to {Money(paise=int(total))}, September to {Money(paise=int(other_month))}"
    )


def _rejected_by_trigger(error: DBAPIError, statement: str) -> tuple[bool, str]:
    """Pass only if the append-only trigger refused it, not something else.

    Same wording trap as step 1.7: a statement can be refused for a reason that
    has nothing to do with append-only, and a check that only asks "did it
    fail?" keeps passing after the protection is gone.
    """
    if "append-only" in str(error):
        return True, f"{statement} rejected by the append-only trigger"
    first_line = str(error).strip().splitlines()[0][:90]
    return False, f"{statement} refused, but not by the trigger: {first_line}"


def _attempt_against_the_ledger(statement: str) -> tuple[bool, str]:
    connection = _engine().connect()
    transaction = connection.begin()
    try:
        connection.execute(INSERT_DRILL_ROW, {"called_at": NOW})
        try:
            connection.execute(text(statement))
        except DBAPIError as error:
            return _rejected_by_trigger(error, statement.split(maxsplit=1)[0])
        return False, f"{statement.split(maxsplit=1)[0]} was allowed - the ledger is editable"
    finally:
        transaction.rollback()
        connection.close()


def _spend_cannot_be_edited_away() -> tuple[bool, str]:
    """A ledger that can be rewritten cannot prove we stayed under the cap."""
    outcomes = [
        _attempt_against_the_ledger("UPDATE llm_calls SET cost_paise = 0"),
        _attempt_against_the_ledger("DELETE FROM llm_calls"),
        _attempt_against_the_ledger("TRUNCATE llm_calls"),
    ]
    failures = [detail for passed, detail in outcomes if not passed]
    if failures:
        return False, "; ".join(failures)
    return True, "UPDATE, DELETE and TRUNCATE all rejected by the trigger"


def run() -> list[CheckResult]:
    checks = [
        (
            "one_rupee_cap_blocks_the_call",
            _a_one_rupee_cap_blocks_the_call,
            "the client must never be reached",
        ),
        (
            "cap_refuses_before_spending",
            _the_cap_refuses_before_spending_not_after,
            "the estimate counts toward the cap, not just spend",
        ),
        ("refused_calls_are_not_billed", _nothing_is_billed_for_a_refused_call, "0 ledger rows"),
        ("cost_reported_per_node", _cost_is_reported_per_node, "a per-node breakdown and a total"),
        (
            "month_boundary_respected",
            _the_month_boundary_is_respected,
            "last month does not count against this month",
        ),
        (
            "recorded_client_is_offline",
            _the_recorded_client_never_reaches_the_network,
            "a cassette miss raises rather than calling out",
        ),
        (
            "tiering_saves_real_money",
            _tiering_is_worth_its_complexity,
            "triage at least 10x cheaper than synthesis",
        ),
        (
            "every_call_is_attributable",
            _the_governor_is_the_only_way_in,
            "node, cycle_id and tier are required",
        ),
        (
            "ledger_round_trips_in_postgres",
            _ledger_round_trips_through_postgres,
            "the month sum reads back what was written",
        ),
        (
            "spend_cannot_be_edited_away",
            _spend_cannot_be_edited_away,
            "UPDATE, DELETE and TRUNCATE all refused by the trigger",
        ),
        (
            "rates_reconciled_with_console",
            _rates_reconciled_against_the_console,
            "config/llm_pricing.yaml verified: true",
        ),
    ]

    results = []
    for name, check, requirement in checks:
        passed, measured = check()
        results.append(
            CheckResult(name=name, passed=passed, measured=measured, requirement=requirement)
        )
    return results
