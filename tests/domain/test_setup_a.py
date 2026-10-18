"""Setup A's nine gates, with a boundary test on each side of every threshold.

`docs/10` task 4: "Boundary tests at exactly the threshold on each side." An
off-by-one in a gate is invisible in normal operation and changes which ideas
get briefed, so each numeric gate is tested at the threshold, just inside and
just outside.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from trading_desk.domain.enums import Direction, Verdict
from trading_desk.domain.setups.setup_a import (
    GATES,
    LegLiquidity,
    SetupACandidate,
    calendar,
    catalyst_recency,
    catalyst_type_allowed,
    credibility,
    evaluate,
    governance_clear,
    liquidity,
    not_already_priced,
    technical_confirm,
    volatility_not_elevated,
)
from trading_desk.domain.setups.thresholds import load_setup_thresholds
from trading_desk.domain.values import Money

LIMITS = load_setup_thresholds().setup_a
EXPECTED_GATE_COUNT = 9


def _good_leg() -> LegLiquidity:
    return LegLiquidity(
        open_interest_lots=1000,
        bid=Money.from_rupees(Decimal("99")),
        ask=Money.from_rupees(Decimal("101")),
    )


def _candidate(**overrides: object) -> SetupACandidate:
    base = {
        "instrument_symbol": "RELIANCE",
        "direction": Direction.BULLISH,
        "sessions_since_catalyst": 1,
        "catalyst_type": "LARGE_ORDER_WIN",
        "credibility": 0.85,
        "move_since_catalyst": Money.from_rupees(Decimal("10")),
        "atr": Money.from_rupees(Decimal("20")),
        "close_beyond_range_edge": True,
        "volume_ratio": 2.0,
        "iv_rank": 40.0,
        "legs": (_good_leg(), _good_leg()),
        "days_to_expiry": 15,
    }
    base.update(overrides)
    return SetupACandidate(**base)  # type: ignore[arg-type]


class TestBoundaries:
    @pytest.mark.parametrize(
        ("sessions", "expected"),
        [(1, Verdict.PASS), (2, Verdict.PASS), (3, Verdict.FAIL)],
    )
    def test_catalyst_recency_at_the_boundary(self, sessions: int, expected: Verdict) -> None:
        result = catalyst_recency(_candidate(sessions_since_catalyst=sessions), LIMITS)
        assert result.verdict is expected

    @pytest.mark.parametrize(
        ("score", "expected"),
        [(0.69, Verdict.FAIL), (0.70, Verdict.PASS), (0.71, Verdict.PASS)],
    )
    def test_credibility_at_exactly_the_threshold(self, score: float, expected: Verdict) -> None:
        """0.70 passes: the rule is >= 0.7, not > 0.7."""
        assert credibility(_candidate(credibility=score), LIMITS).verdict is expected

    @pytest.mark.parametrize(
        ("move", "expected"),
        # ATR is 20, so 1.5 x ATR is exactly 30. The rule is "< 1.5 x ATR".
        [("29.99", Verdict.PASS), ("30", Verdict.FAIL), ("30.01", Verdict.FAIL)],
    )
    def test_not_already_priced_at_exactly_one_and_a_half_atr(
        self, move: str, expected: Verdict
    ) -> None:
        candidate = _candidate(move_since_catalyst=Money.from_rupees(Decimal(move)))
        assert not_already_priced(candidate, LIMITS).verdict is expected

    @pytest.mark.parametrize(
        ("ratio", "expected"),
        [(1.49, Verdict.FAIL), (1.5, Verdict.PASS), (1.51, Verdict.PASS)],
    )
    def test_volume_ratio_at_the_boundary(self, ratio: float, expected: Verdict) -> None:
        assert technical_confirm(_candidate(volume_ratio=ratio), LIMITS).verdict is expected

    @pytest.mark.parametrize(
        ("rank", "expected"),
        [(59.9, Verdict.PASS), (60.0, Verdict.FAIL), (60.1, Verdict.FAIL)],
    )
    def test_iv_rank_at_exactly_sixty(self, rank: float, expected: Verdict) -> None:
        """The rule is "< 60", so 60.0 itself fails."""
        assert volatility_not_elevated(_candidate(iv_rank=rank), LIMITS).verdict is expected

    @pytest.mark.parametrize(
        ("dte", "expected"),
        [(9, Verdict.FAIL), (10, Verdict.PASS), (25, Verdict.PASS), (26, Verdict.FAIL)],
    )
    def test_expiry_window_is_inclusive_at_both_ends(self, dte: int, expected: Verdict) -> None:
        assert calendar(_candidate(days_to_expiry=dte), LIMITS).verdict is expected

    @pytest.mark.parametrize(
        ("oi", "expected"),
        [(499, Verdict.FAIL), (500, Verdict.PASS), (501, Verdict.PASS)],
    )
    def test_open_interest_at_exactly_five_hundred_lots(self, oi: int, expected: Verdict) -> None:
        leg = LegLiquidity(
            open_interest_lots=oi,
            bid=Money.from_rupees(Decimal("99")),
            ask=Money.from_rupees(Decimal("101")),
        )
        assert liquidity(_candidate(legs=(leg, _good_leg())), LIMITS).verdict is expected


class TestMissingDataBlocksRatherThanPasses:
    @pytest.mark.parametrize(
        "missing",
        [
            "sessions_since_catalyst",
            "catalyst_type",
            "credibility",
            "volume_ratio",
            "iv_rank",
            "days_to_expiry",
        ],
    )
    def test_an_absent_input_is_no_data_not_a_pass(self, missing: str) -> None:
        """Rule 6. A gate with nothing to judge must not wave the idea through."""
        report = evaluate(_candidate(**{missing: None}), LIMITS)

        assert not report.approved
        assert any(result.verdict is Verdict.NO_DATA for result in report.results)

    def test_no_data_is_distinct_from_fail(self) -> None:
        """ "No IV rank" and "IV rank too high" are different facts."""
        absent = volatility_not_elevated(_candidate(iv_rank=None), LIMITS)
        rejected = volatility_not_elevated(_candidate(iv_rank=75.0), LIMITS)

        assert absent.verdict is Verdict.NO_DATA
        assert rejected.verdict is Verdict.FAIL

    def test_a_zero_atr_cannot_produce_a_division(self) -> None:
        candidate = _candidate(atr=Money.zero())
        assert not_already_priced(candidate, LIMITS).verdict is Verdict.NO_DATA

    def test_a_leg_with_no_mid_is_no_data_not_a_failure(self) -> None:
        dead = LegLiquidity(open_interest_lots=1000, bid=Money.zero(), ask=Money.zero())
        assert liquidity(_candidate(legs=(dead,)), LIMITS).verdict is Verdict.NO_DATA


class TestTheRulebook:
    def test_an_unlisted_catalyst_type_fails(self) -> None:
        """An LLM will call anything a catalyst. The list is closed."""
        result = catalyst_type_allowed(_candidate(catalyst_type="ANALYST_UPGRADE"), LIMITS)
        assert result.verdict is Verdict.FAIL

    def test_price_and_volume_must_both_confirm(self) -> None:
        no_break = technical_confirm(_candidate(close_beyond_range_edge=False), LIMITS)
        no_volume = technical_confirm(_candidate(volume_ratio=1.0), LIMITS)

        assert no_break.verdict is Verdict.FAIL
        assert no_volume.verdict is Verdict.FAIL

    def test_results_inside_the_window_disqualify(self) -> None:
        candidate = _candidate(results_date_in_window=date(2026, 11, 5))
        assert calendar(candidate, LIMITS).verdict is Verdict.FAIL

    def test_unless_results_are_themselves_the_catalyst(self) -> None:
        candidate = _candidate(
            results_date_in_window=date(2026, 11, 5),
            catalyst_is_results=True,
            catalyst_type="EARNINGS_SURPRISE",
        )
        assert calendar(candidate, LIMITS).verdict is Verdict.PASS

    def test_the_ban_list_blocks_regardless_of_the_window(self) -> None:
        candidate = _candidate(days_to_expiry=15, in_ban_list=True)
        assert calendar(candidate, LIMITS).verdict is Verdict.FAIL

    def test_one_governance_flag_is_a_veto(self) -> None:
        candidate = _candidate(governance_flags=("auditor resigned",))
        assert governance_clear(candidate, LIMITS).verdict is Verdict.FAIL


class TestTheCombinator:
    def test_a_clean_candidate_passes_every_gate(self) -> None:
        report = evaluate(_candidate(), LIMITS)

        assert report.approved
        assert len(report.results) == EXPECTED_GATE_COUNT

    def test_every_gate_runs_even_after_one_fails(self) -> None:
        """No short-circuit: the briefing shows the whole picture, and one
        narrow miss reads differently from six bad ones."""
        report = evaluate(_candidate(credibility=0.1, iv_rank=90.0, volume_ratio=0.2), LIMITS)

        assert len(report.results) == EXPECTED_GATE_COUNT
        assert len(report.blockers()) == 3

    def test_every_result_carries_its_measurement_and_threshold(self) -> None:
        """Rule 9. A gate that answers only "no" cannot be debugged later."""
        for result in evaluate(_candidate(), LIMITS).results:
            assert result.measured, f"{result.name} reported no measurement"
            assert result.threshold, f"{result.name} reported no threshold"

    def test_the_gate_list_is_the_nine_from_the_rulebook(self) -> None:
        assert len(GATES) == EXPECTED_GATE_COUNT

    def test_gate_values_are_collected_for_the_journal(self) -> None:
        values = evaluate(_candidate(), LIMITS).values()

        assert len(values) == EXPECTED_GATE_COUNT
        assert "iv_rank" in values

    def test_a_proxy_iv_rank_is_labelled_in_the_measurement(self) -> None:
        result = volatility_not_elevated(_candidate(iv_rank_is_proxy=True), LIMITS)
        assert "proxy" in result.measured
