"""Verify step 2.3 - Setup A gates.

Nine gates, each returning a measured value and the threshold it was compared
against (rule 9), none short-circuiting, all thresholds from
`config/setups.yaml`.

**`verified: false` in that config, and this step fails on it by design**, the
same way `verify 1_2` fails on the unsigned liquidity thresholds. The numbers
come from `docs/06` and are a considered starting point, not a backtested one.
Step 2.8 runs the dry run; until then every threshold is a hypothesis and the
check says so rather than letting the setup look finished.
"""

from __future__ import annotations

from decimal import Decimal

from scripts.verify._runner import CheckResult
from trading_desk.domain.enums import Direction, Verdict
from trading_desk.domain.setups.setup_a import (
    GATES,
    LegLiquidity,
    SetupACandidate,
    evaluate,
)
from trading_desk.domain.setups.thresholds import load_setup_thresholds
from trading_desk.domain.values import Money

TITLE = "Setup A gates"

THRESHOLDS = load_setup_thresholds()
LIMITS = THRESHOLDS.setup_a
EXPECTED_GATES = 9


def _leg() -> LegLiquidity:
    return LegLiquidity(
        open_interest_lots=1000,
        bid=Money.from_rupees(Decimal("99")),
        ask=Money.from_rupees(Decimal("101")),
    )


def _clean() -> SetupACandidate:
    return SetupACandidate(
        instrument_symbol="RELIANCE",
        direction=Direction.BULLISH,
        sessions_since_catalyst=1,
        catalyst_type="LARGE_ORDER_WIN",
        credibility=0.85,
        move_since_catalyst=Money.from_rupees(Decimal("10")),
        atr=Money.from_rupees(Decimal("20")),
        close_beyond_range_edge=True,
        volume_ratio=2.0,
        iv_rank=40.0,
        legs=(_leg(), _leg()),
        days_to_expiry=15,
    )


def _nine_gates_exist() -> tuple[bool, str]:
    names = [gate.__name__ for gate in GATES]
    return len(GATES) == EXPECTED_GATES, f"{len(GATES)} gates: {names}"


def _a_clean_candidate_passes() -> tuple[bool, str]:
    report = evaluate(_clean(), LIMITS)
    return report.approved, report.summary()


def _every_result_reports_a_value_and_a_threshold() -> tuple[bool, str]:
    """Rule 9: gates return values, not booleans."""
    bare = [
        result.name
        for result in evaluate(_clean(), LIMITS).results
        if not result.measured or not result.threshold
    ]
    return not bare, (
        f"all {EXPECTED_GATES} gates reported a measurement and a threshold"
        if not bare
        else f"reported no numbers: {bare}"
    )


def _nothing_short_circuits() -> tuple[bool, str]:
    """Three independent failures must all be visible, not just the first."""
    import dataclasses  # noqa: PLC0415 - local, verify-only

    broken = dataclasses.replace(_clean(), credibility=0.1, iv_rank=90.0, volume_ratio=0.2)
    report = evaluate(broken, LIMITS)
    blockers = report.blockers()
    expected_blockers = 3
    return (
        len(report.results) == EXPECTED_GATES and len(blockers) == expected_blockers,
        f"{len(report.results)} gates ran, {len(blockers)} blocked: "
        f"{[item.name for item in blockers]}",
    )


def _boundaries_land_on_the_documented_side() -> tuple[bool, str]:
    """The rules are >= 0.7 credibility, < 1.5 ATR, < 60 IV rank. Each is
    checked at the threshold itself, which is where an off-by-one hides."""
    import dataclasses  # noqa: PLC0415

    from trading_desk.domain.setups.setup_a import (  # noqa: PLC0415
        credibility,
        not_already_priced,
        volatility_not_elevated,
    )

    base = _clean()
    checks = {
        "credibility 0.70 passes": credibility(
            dataclasses.replace(base, credibility=0.70), LIMITS
        ).verdict
        is Verdict.PASS,
        "credibility 0.69 fails": credibility(
            dataclasses.replace(base, credibility=0.69), LIMITS
        ).verdict
        is Verdict.FAIL,
        "move 1.5x ATR fails": not_already_priced(
            dataclasses.replace(base, move_since_catalyst=Money.from_rupees(Decimal("30"))),
            LIMITS,
        ).verdict
        is Verdict.FAIL,
        "IV rank 60.0 fails": volatility_not_elevated(
            dataclasses.replace(base, iv_rank=60.0), LIMITS
        ).verdict
        is Verdict.FAIL,
        "IV rank 59.9 passes": volatility_not_elevated(
            dataclasses.replace(base, iv_rank=59.9), LIMITS
        ).verdict
        is Verdict.PASS,
    }
    wrong = [name for name, correct in checks.items() if not correct]
    return not wrong, f"{len(checks)} boundary cases, all on the documented side"


def _missing_data_blocks_rather_than_passes() -> tuple[bool, str]:
    """Rule 6. A gate with nothing to judge must not wave an idea through."""
    import dataclasses  # noqa: PLC0415

    blank = dataclasses.replace(_clean(), iv_rank=None, credibility=None)
    report = evaluate(blank, LIMITS)
    no_data_count = sum(1 for item in report.results if item.verdict is Verdict.NO_DATA)
    return not report.approved and no_data_count == 2, (  # noqa: PLR2004
        f"{no_data_count} gates returned NO_DATA, candidate not approved"
    )


def _thresholds_come_from_config() -> tuple[bool, str]:
    return True, (
        f"credibility >= {LIMITS.min_credibility}, move < {LIMITS.max_move_in_atr}x ATR, "
        f"IV rank < {LIMITS.max_iv_rank}, OI >= {LIMITS.liquidity.min_open_interest_lots} "
        f"lots, {LIMITS.expiry.min_days}-{LIMITS.expiry.max_days} DTE"
    )


def _thresholds_are_signed_off() -> tuple[bool, str]:
    return THRESHOLDS.verified, f"verified={THRESHOLDS.verified} ({THRESHOLDS.source})"


def run() -> list[CheckResult]:
    count_ok, count_detail = _nine_gates_exist()
    clean_ok, clean_detail = _a_clean_candidate_passes()
    values_ok, values_detail = _every_result_reports_a_value_and_a_threshold()
    short_ok, short_detail = _nothing_short_circuits()
    bounds_ok, bounds_detail = _boundaries_land_on_the_documented_side()
    missing_ok, missing_detail = _missing_data_blocks_rather_than_passes()
    config_ok, config_detail = _thresholds_come_from_config()
    signed_ok, signed_detail = _thresholds_are_signed_off()

    return [
        CheckResult(
            name="nine_independently_testable_gates_exist",
            passed=count_ok,
            measured=count_detail,
            requirement=f"need {EXPECTED_GATES}, one per rule in docs/06",
        ),
        CheckResult(
            name="a_clean_candidate_passes_every_gate",
            passed=clean_ok,
            measured=clean_detail,
            requirement="need all gates PASS on a candidate built to satisfy them",
        ),
        CheckResult(
            name="every_gate_reports_a_value_and_a_threshold",
            passed=values_ok,
            measured=values_detail,
            requirement="need numbers, never a bare boolean (rule 9)",
        ),
        CheckResult(
            name="the_combinator_does_not_short_circuit",
            passed=short_ok,
            measured=short_detail,
            requirement="need all 9 to run and all 3 failures visible",
        ),
        CheckResult(
            name="boundaries_land_on_the_documented_side",
            passed=bounds_ok,
            measured=bounds_detail,
            requirement="need >= 0.7, < 1.5x ATR and < 60 exactly as docs/06 states",
        ),
        CheckResult(
            name="missing_data_blocks_rather_than_passes",
            passed=missing_ok,
            measured=missing_detail,
            requirement="need NO_DATA and no approval (rule 6)",
        ),
        CheckResult(
            name="thresholds_are_read_from_config",
            passed=config_ok,
            measured=config_detail,
            requirement="need config/setups.yaml, not literals in code (rule 7)",
        ),
        CheckResult(
            name="thresholds_are_signed_off",
            passed=signed_ok,
            measured=signed_detail,
            requirement="need verified: true after the step 2.8 dry run",
        ),
    ]
