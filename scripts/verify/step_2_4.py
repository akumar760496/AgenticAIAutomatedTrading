"""Verify step 2.4 - Setup B gates.

`docs/10` task 4: "Test the middle band explicitly - it's the most common
outcome and the easiest to accidentally break." So the middle band gets its
own checks rather than being the fall-through case.

The bands deliberately do not tile the number line: `>= 70` credit, `<= 35`
debit, and 35-70 no-trade, with **35 and 70 themselves tradable**. That
asymmetry is from docs/06 and is exactly the kind of boundary that gets
"tidied" into a different rulebook by accident, so each edge is checked.

`verify 2_4` also asserts that a naked short is unreachable - FR-5.9 - by
confirming the config flag is off *and* that the credit builder refuses to
emit one when no wing exists.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from scripts.verify._runner import CheckResult
from trading_desk.domain.enums import Direction, Exchange, OptionRight, Segment, Verdict
from trading_desk.domain.instruments import InstrumentRef
from trading_desk.domain.market import OptionChainSnapshot, OptionQuote, OptionStrike
from trading_desk.domain.options.structure import StructureError, build_credit_vertical
from trading_desk.domain.setups.setup_b import (
    IndexVariant,
    SetupBCandidate,
    classify_regime,
    evaluate,
)
from trading_desk.domain.setups.thresholds import DeltaBand, load_setup_thresholds
from trading_desk.domain.values import Money, Price

TITLE = "Setup B gates"

THRESHOLDS = load_setup_thresholds()
LIMITS = THRESHOLDS.setup_b
MIDDLE_BAND_SAMPLES = (35.1, 40.0, 50.0, 60.0, 69.9)


def _candidate(**overrides: object) -> SetupBCandidate:
    base = {
        "instrument_symbol": "NIFTY",
        "iv_rank": 80.0,
        "days_to_expiry": 6,
        "sessions_to_expiry": 5,
    }
    base.update(overrides)
    return SetupBCandidate(**base)  # type: ignore[arg-type]


def _the_middle_band_is_always_no_trade() -> tuple[bool, str]:
    verdicts = {
        rank: classify_regime(_candidate(iv_rank=rank), LIMITS).variant
        for rank in MIDDLE_BAND_SAMPLES
    }
    wrong = {
        rank: str(variant)
        for rank, variant in verdicts.items()
        if variant is not IndexVariant.NO_TRADE
    }
    return not wrong, (
        f"IV ranks {list(MIDDLE_BAND_SAMPLES)} all classify as NO_TRADE"
        if not wrong
        else f"not no-trade: {wrong}"
    )


def _a_trend_does_not_unlock_the_middle_band() -> tuple[bool, str]:
    """Trend only unlocks the *low* band. Mixing them widens B2 into no-trade."""
    decision = classify_regime(_candidate(iv_rank=50.0, is_trending=True), LIMITS)
    return decision.variant is IndexVariant.NO_TRADE, (
        f"IV rank 50 while trending -> {decision.variant}"
    )


def _the_band_edges_are_tradable() -> tuple[bool, str]:
    """>= 70 and <= 35, so both edges themselves trade."""
    at_70 = classify_regime(_candidate(iv_rank=70.0), LIMITS).variant
    just_under = classify_regime(_candidate(iv_rank=69.9), LIMITS).variant
    at_35 = classify_regime(_candidate(iv_rank=35.0, is_trending=True), LIMITS).variant
    just_over = classify_regime(_candidate(iv_rank=35.1, is_trending=True), LIMITS).variant

    correct = (
        at_70 is IndexVariant.CREDIT
        and just_under is IndexVariant.NO_TRADE
        and at_35 is IndexVariant.DEBIT
        and just_over is IndexVariant.NO_TRADE
    )
    return correct, f"70.0 -> {at_70}, 69.9 -> {just_under}, 35.0 -> {at_35}, 35.1 -> {just_over}"


def _no_iv_rank_is_unknown_not_no_trade() -> tuple[bool, str]:
    decision = classify_regime(_candidate(iv_rank=None), LIMITS)
    return decision.variant is IndexVariant.UNKNOWN, (
        f"{decision.variant} - distinct from NO_TRADE, because waiting for "
        "history fixes one and not the other"
    )


def _no_position_is_opened_into_expiry() -> tuple[bool, str]:
    from trading_desk.domain.setups.setup_b import flat_before_expiry  # noqa: PLC0415

    at_limit = flat_before_expiry(_candidate(sessions_to_expiry=1), LIMITS).verdict
    clear = flat_before_expiry(_candidate(sessions_to_expiry=2), LIMITS).verdict
    return at_limit is Verdict.FAIL and clear is Verdict.PASS, (
        f"1 session -> {at_limit}, 2 sessions -> {clear}"
    )


def _every_macro_event_blocks() -> tuple[bool, str]:
    from trading_desk.domain.setups.setup_b import no_blocking_event  # noqa: PLC0415

    missed = [
        event
        for event in sorted(LIMITS.blocking_event_types)
        if no_blocking_event(_candidate(events_in_window=(event,)), LIMITS).verdict
        is not Verdict.FAIL
    ]
    return not missed, (
        f"all {len(LIMITS.blocking_event_types)} blocking events refused entry"
        if not missed
        else f"did not block: {missed}"
    )


def _only_one_index_position() -> tuple[bool, str]:
    report = evaluate(_candidate(open_index_positions=1), LIMITS)
    blocker = next((item for item in report.results if item.name == "position_limit"), None)
    return blocker is not None and blocker.verdict is Verdict.FAIL, (
        f"a second index position is refused (limit {LIMITS.max_concurrent_index_positions})"
    )


def _a_naked_short_is_unreachable() -> tuple[bool, str]:
    """FR-5.9, checked two ways: the flag is off, and the builder refuses."""
    if THRESHOLDS.allow_naked_shorts:
        return False, "allow_naked_shorts is true in config"

    nifty = InstrumentRef(
        symbol="NIFTY",
        exchange=Exchange.NSE,
        segment=Segment.FNO,
        security_id="26000",
        lot_size=75,
    )
    quote = OptionQuote(
        right=OptionRight.PUT,
        last=Price.from_rupees(Decimal("90")),
        bid=Price.from_rupees(Decimal("89")),
        ask=Price.from_rupees(Decimal("91")),
        volume=100,
        open_interest=1000,
        implied_volatility=18.0,
        delta=-0.35,
    )
    # A chain with exactly one priced put: there is nothing to buy as a wing.
    lonely = OptionChainSnapshot(
        underlying=nifty,
        expiry_date="2026-11-26",
        strikes=(OptionStrike(strike=Price.from_rupees(Decimal("23800")), call=None, put=quote),),
        spot=Price.from_rupees(Decimal("24000")),
        exchange_time=datetime.now(UTC),
        received_at=datetime.now(UTC),
    )
    try:
        build_credit_vertical(
            lonely,
            Direction.BULLISH,
            DeltaBand(0.30, 0.40),
            wing_offset=Money.from_rupees(Decimal("200")),
        )
    except StructureError as error:
        return "naked short" in str(error), "builder refuses a credit spread with no wing"
    return False, "a credit structure was built with no protective wing"


def run() -> list[CheckResult]:
    middle_ok, middle_detail = _the_middle_band_is_always_no_trade()
    trend_ok, trend_detail = _a_trend_does_not_unlock_the_middle_band()
    edges_ok, edges_detail = _the_band_edges_are_tradable()
    unknown_ok, unknown_detail = _no_iv_rank_is_unknown_not_no_trade()
    expiry_ok, expiry_detail = _no_position_is_opened_into_expiry()
    macro_ok, macro_detail = _every_macro_event_blocks()
    position_ok, position_detail = _only_one_index_position()
    naked_ok, naked_detail = _a_naked_short_is_unreachable()

    return [
        CheckResult(
            name="the_middle_band_is_always_no_trade",
            passed=middle_ok,
            measured=middle_detail,
            requirement="need NO_TRADE across 35-70 - the most common output",
        ),
        CheckResult(
            name="a_trend_does_not_unlock_the_middle_band",
            passed=trend_ok,
            measured=trend_detail,
            requirement="need NO_TRADE - trend unlocks the low band only",
        ),
        CheckResult(
            name="the_band_edges_are_tradable_as_documented",
            passed=edges_ok,
            measured=edges_detail,
            requirement="need 70 credit, 69.9 no-trade, 35 debit, 35.1 no-trade",
        ),
        CheckResult(
            name="no_iv_rank_is_unknown_not_no_trade",
            passed=unknown_ok,
            measured=unknown_detail,
            requirement="need UNKNOWN - a gap and a decision are different facts",
        ),
        CheckResult(
            name="no_position_is_opened_into_expiry",
            passed=expiry_ok,
            measured=expiry_detail,
            requirement="need entry refused at 1 session to expiry",
        ),
        CheckResult(
            name="every_macro_event_blocks_entry",
            passed=macro_ok,
            measured=macro_detail,
            requirement="need RBI, CPI, IIP, FOMC and Budget all to block",
        ),
        CheckResult(
            name="only_one_index_position_at_a_time",
            passed=position_ok,
            measured=position_detail,
            requirement="need a second position refused",
        ),
        CheckResult(
            name="a_naked_short_is_unreachable_in_code",
            passed=naked_ok,
            measured=naked_detail,
            requirement="need the builder to raise, not merely the flag to be off (FR-5.9)",
        ),
    ]
