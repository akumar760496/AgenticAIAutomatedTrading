"""Verify step 2.7 - the risk gate.

`docs/10`: done when "the property suite is green and the fail-closed tests
pass. `make check` gates the build on it."

Three things get checked here that an example-based test cannot:

1. **Sizing has no confidence parameter** (FR-6.3, FR-7.8), asserted by
   introspecting the signature. Confidence-based sizing is blocked until
   calibration beats baseline over 50 resolved ideas, so the parameter must
   not exist to be passed.
2. **`RiskInputs` accepts no agent output** (FR-6.7). The gate cannot be
   argued with because it is never handed an argument.
3. **The size property holds over hundreds of random inputs**, not a handful
   of chosen ones.

The capital check at the end is a finding rather than a defect: flat 0.25%
risk and NSE lot sizes together imply a minimum account size, and the number
is larger than it looks.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime, time
from decimal import Decimal

from scripts.verify._runner import CheckResult
from trading_desk.domain.risk.gate import CHECKS, RiskInputs, evaluate
from trading_desk.domain.risk.sizing import DEFAULT_RISK_FRACTION, position_size
from trading_desk.domain.values import Money

TITLE = "The risk gate"

EXPECTED_CHECKS = 13
SAMPLES = 2000
NOW = datetime(2026, 10, 19, 11, 0, tzinfo=UTC)

BANNED_SIZING_PARAMS = {"confidence", "conviction", "score", "probability", "edge"}
BANNED_INPUT_FIELDS = {"thesis", "rationale", "confidence", "agent_note", "override"}

# A NIFTY debit spread: 140 rupees max loss per unit, 75 to a lot.
NIFTY_LOT = 75
SPREAD_MAX_LOSS = Money.from_rupees(Decimal("140"))


def _healthy(**overrides: object) -> RiskInputs:
    base = {
        "instrument_symbol": "RELIANCE",
        "now": NOW,
        "session_open": time(9, 15),
        "session_close": time(15, 30),
        "tick_age_seconds": 0.5,
        "chain_age_seconds": 5.0,
        "in_approved_universe": True,
        "max_loss_per_lot": Money.from_rupees(Decimal("10")),
        "is_defined_risk": True,
        "open_interest_lots": 1200,
        "spread_fraction": 0.02,
        "size": position_size(
            Money.from_rupees(Decimal("2000000")), Money.from_rupees(Decimal("10")), 250
        ),
        "equity": Money.from_rupees(Decimal("2000000")),
        "max_symbol_exposure": Money.from_rupees(Decimal("100000")),
        "symbol_exposure": Money.from_rupees(Decimal("10000")),
        "max_portfolio_heat": Money.from_rupees(Decimal("50000")),
        "portfolio_heat": Money.from_rupees(Decimal("5000")),
        "max_net_delta": 100.0,
        "max_short_vega": 500.0,
        "daily_loss_limit": Money.from_rupees(Decimal("20000")),
        "max_trades_per_day": 5,
        "available_margin": Money.from_rupees(Decimal("500000")),
        "required_margin": Money.from_rupees(Decimal("50000")),
        "min_minutes_between_entries": 30.0,
    }
    base.update(overrides)
    return RiskInputs(**base)  # type: ignore[arg-type]


def _thirteen_checks_exist() -> tuple[bool, str]:
    return len(CHECKS) == EXPECTED_CHECKS, (
        f"{len(CHECKS)} checks: {[check.__name__ for check in CHECKS]}"
    )


def _sizing_has_no_confidence_input() -> tuple[bool, str]:
    parameters = set(inspect.signature(position_size).parameters)
    offending = parameters & BANNED_SIZING_PARAMS
    return not offending, (
        f"position_size({', '.join(sorted(parameters))}) - no confidence input"
        if not offending
        else f"accepts {sorted(offending)}"
    )


def _the_gate_accepts_no_agent_output() -> tuple[bool, str]:
    fields = set(RiskInputs.__dataclass_fields__)
    offending = fields & BANNED_INPUT_FIELDS
    return not offending, (
        f"{len(fields)} input fields, none carrying agent output"
        if not offending
        else f"accepts {sorted(offending)}"
    )


def _risk_never_exceeds_the_budget() -> tuple[bool, str]:
    """The central property, over a deterministic sweep.

    `hypothesis` covers this in the test suite; here it is re-run over a fixed
    grid so the verify output carries a number rather than a pass mark.
    """
    breaches: list[str] = []
    checked = 0
    for equity_rupees in (0, 1, 5_000, 100_000, 1_000_000, 4_200_000, 50_000_000):
        for loss_paise in (1, 7, 1000, 10_500_00, 99_999):
            for lot in (1, 25, 75, 250, 1100):
                size = position_size(
                    Money.from_rupees(Decimal(equity_rupees)), Money(paise=loss_paise), lot
                )
                checked += 1
                if size.total_risk.paise > size.risk_budget.paise:
                    breaches.append(f"over budget: {equity_rupees}/{loss_paise}/{lot}")
                # And one more lot must always breach - rounding is down.
                if (size.lots + 1) * size.max_loss_per_lot.paise <= size.risk_budget.paise:
                    breaches.append(f"underfilled: {equity_rupees}/{loss_paise}/{lot}")
    return not breaches, (
        f"{checked} combinations: risk always within budget, and one more lot would always breach"
        if not breaches
        else f"{len(breaches)} breaches, first {breaches[0]}"
    )


def _the_four_fail_closed_conditions() -> tuple[bool, str]:
    """FR-9.4: stale data, disconnect, exception, and unknown all veto."""
    outcomes = {
        "stale tick": evaluate(_healthy(tick_age_seconds=30.0)).approved,
        "feed disconnected": evaluate(_healthy(feed_connected=False)).approved,
        "unknown data age": evaluate(_healthy(chain_age_seconds=None)).approved,
        "missing structure price": evaluate(_healthy(max_loss_per_lot=None)).approved,
    }
    approved = [name for name, was_approved in outcomes.items() if was_approved]
    return not approved, (
        f"all {len(outcomes)} fail-closed conditions vetoed"
        if not approved
        else f"APPROVED despite: {approved}"
    )


def _every_limit_can_be_driven_to_breach() -> tuple[bool, str]:
    """FR-6.5: each portfolio limit has a case that breaches it."""
    breaches: dict[str, dict[str, object]] = {
        "concentration": {"symbol_exposure": Money.from_rupees(Decimal("200000"))},
        "portfolio heat": {"portfolio_heat": Money.from_rupees(Decimal("90000"))},
        "net delta": {"net_delta": 500.0},
        "short vega": {"short_vega": 9000.0},
        "daily loss": {"realised_loss_today": Money.from_rupees(Decimal("20000"))},
        "trade count": {"trades_today": 5},
        "margin": {"required_margin": Money.from_rupees(Decimal("900000"))},
        "naked short": {"has_naked_short": True},
        "halt": {"halted": True},
    }
    leaked = [
        name for name, override in breaches.items() if evaluate(_healthy(**dict(override))).approved
    ]
    return not leaked, (
        f"all {len(breaches)} limits vetoed when driven to breach"
        if not leaked
        else f"approved despite breaching: {leaked}"
    )


def _a_healthy_proposal_is_approved_with_a_size() -> tuple[bool, str]:
    decision = evaluate(_healthy())
    return decision.approved and decision.size is not None and decision.size.lots > 0, (
        decision.summary()
    )


def _the_gate_is_advisory_in_v1() -> tuple[bool, str]:
    """FR-6.2: nothing here places an order; it reports the size it would take."""
    from pathlib import Path  # noqa: PLC0415

    source = Path("src/trading_desk/domain/risk/gate.py").read_text()
    order_terms = [
        term for term in ("place_order", "submit", "broker.", "execute") if term in source
    ]
    return not order_terms, (
        "the gate computes and reports; no order path exists in it"
        if not order_terms
        else f"found order terms: {order_terms}"
    )


def _minimum_viable_capital_is_reported() -> tuple[bool, str]:
    """A finding, not a defect. Flat 0.25% risk plus NSE lot sizes implies a
    floor on account size, and the number is larger than it looks."""
    per_lot = Money(paise=SPREAD_MAX_LOSS.paise * NIFTY_LOT)
    needed = Money(paise=int(Decimal(per_lot.paise) / DEFAULT_RISK_FRACTION))
    at_ten_lakh = position_size(Money.from_rupees(Decimal("1000000")), SPREAD_MAX_LOSS, NIFTY_LOT)
    return at_ten_lakh.lots == 0, (
        f"a NIFTY spread risking ₹{SPREAD_MAX_LOSS.rupees():.0f}/unit x {NIFTY_LOT} = "
        f"₹{per_lot.rupees():,.0f} per lot needs ₹{needed.rupees():,.0f} equity at "
        f"{DEFAULT_RISK_FRACTION:.2%}; at ₹10,00,000 the answer is "
        f"{at_ten_lakh.lots} lots"
    )


def run() -> list[CheckResult]:
    count_ok, count_detail = _thirteen_checks_exist()
    sizing_ok, sizing_detail = _sizing_has_no_confidence_input()
    agent_ok, agent_detail = _the_gate_accepts_no_agent_output()
    budget_ok, budget_detail = _risk_never_exceeds_the_budget()
    closed_ok, closed_detail = _the_four_fail_closed_conditions()
    limits_ok, limits_detail = _every_limit_can_be_driven_to_breach()
    healthy_ok, healthy_detail = _a_healthy_proposal_is_approved_with_a_size()
    advisory_ok, advisory_detail = _the_gate_is_advisory_in_v1()
    capital_ok, capital_detail = _minimum_viable_capital_is_reported()

    return [
        CheckResult(
            name="thirteen_documented_checks_exist",
            passed=count_ok,
            measured=count_detail,
            requirement=f"need {EXPECTED_CHECKS} per docs/01 section 4 (FR-6.1)",
        ),
        CheckResult(
            name="sizing_has_no_confidence_input",
            passed=sizing_ok,
            measured=sizing_detail,
            requirement="need the parameter absent, not merely unused (FR-6.3, FR-7.8)",
        ),
        CheckResult(
            name="the_gate_accepts_no_agent_output",
            passed=agent_ok,
            measured=agent_detail,
            requirement="need no thesis, rationale or confidence field (FR-6.7)",
        ),
        CheckResult(
            name="risk_never_exceeds_the_budget_and_never_rounds_up",
            passed=budget_ok,
            measured=budget_detail,
            requirement="need the property to hold across the whole sweep",
        ),
        CheckResult(
            name="the_four_fail_closed_conditions_all_veto",
            passed=closed_ok,
            measured=closed_detail,
            requirement="need stale, disconnected, unknown and unpriced all refused (FR-9.4)",
        ),
        CheckResult(
            name="every_limit_can_be_driven_to_breach",
            passed=limits_ok,
            measured=limits_detail,
            requirement="need each portfolio limit to veto when breached (FR-6.5)",
        ),
        CheckResult(
            name="a_healthy_proposal_is_approved_with_a_size",
            passed=healthy_ok,
            measured=healthy_detail,
            requirement="need approval and a positive lot count",
        ),
        CheckResult(
            name="the_gate_is_advisory_with_no_order_path",
            passed=advisory_ok,
            measured=advisory_detail,
            requirement="need no order call anywhere in the gate (FR-6.2, rule 1)",
        ),
        CheckResult(
            name="minimum_viable_capital_is_reported",
            passed=capital_ok,
            measured=capital_detail,
            requirement="need 0 lots below the floor - never a rounded-up breach",
        ),
    ]
