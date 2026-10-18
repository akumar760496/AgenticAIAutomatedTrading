"""Setup B. The middle band gets the most attention, because it is the most
common answer and the easiest thing to break with a tidy-up."""

from __future__ import annotations

import pytest

from trading_desk.domain.enums import Verdict
from trading_desk.domain.setups.setup_b import (
    IndexVariant,
    SetupBCandidate,
    classify_regime,
    evaluate,
    expiry_window,
    flat_before_expiry,
    no_blocking_event,
    one_index_position,
)
from trading_desk.domain.setups.thresholds import load_setup_thresholds

LIMITS = load_setup_thresholds().setup_b


def _candidate(**overrides: object) -> SetupBCandidate:
    base = {
        "instrument_symbol": "NIFTY",
        "iv_rank": 80.0,
        "is_trending": False,
        "days_to_expiry": 6,
        "sessions_to_expiry": 5,
        "events_in_window": (),
        "open_index_positions": 0,
    }
    base.update(overrides)
    return SetupBCandidate(**base)  # type: ignore[arg-type]


class TestTheMiddleBand:
    """docs/06: "a valid and frequent output". Tested hardest for that reason."""

    @pytest.mark.parametrize("rank", [35.1, 40.0, 50.0, 60.0, 69.9])
    def test_the_whole_middle_band_is_no_trade(self, rank: float) -> None:
        decision = classify_regime(_candidate(iv_rank=rank), LIMITS)

        assert decision.variant is IndexVariant.NO_TRADE
        assert "no-trade band" in decision.reason

    def test_no_trade_still_reports_the_number_it_decided_on(self) -> None:
        decision = classify_regime(_candidate(iv_rank=50.0), LIMITS)

        assert decision.iv_rank == 50.0
        assert not decision.is_tradable

    def test_a_trending_index_in_the_middle_band_is_still_no_trade(self) -> None:
        """Trend only unlocks the *low* band. Mixing them would widen B2 into
        the no-trade zone, which is the tidy-up this guards against."""
        decision = classify_regime(_candidate(iv_rank=50.0, is_trending=True), LIMITS)

        assert decision.variant is IndexVariant.NO_TRADE


class TestRegimeBoundaries:
    @pytest.mark.parametrize(
        ("rank", "expected"),
        [
            (69.9, IndexVariant.NO_TRADE),
            (70.0, IndexVariant.CREDIT),  # >= 70, so 70 itself trades
            (70.1, IndexVariant.CREDIT),
        ],
    )
    def test_the_credit_boundary_is_inclusive(self, rank: float, expected: IndexVariant) -> None:
        assert classify_regime(_candidate(iv_rank=rank), LIMITS).variant is expected

    @pytest.mark.parametrize(
        ("rank", "expected"),
        [
            (34.9, IndexVariant.DEBIT),
            (35.0, IndexVariant.DEBIT),  # <= 35, so 35 itself trades
            (35.1, IndexVariant.NO_TRADE),
        ],
    )
    def test_the_debit_boundary_is_inclusive(self, rank: float, expected: IndexVariant) -> None:
        candidate = _candidate(iv_rank=rank, is_trending=True)
        assert classify_regime(candidate, LIMITS).variant is expected

    def test_low_iv_without_a_trend_is_no_trade_not_debit(self) -> None:
        decision = classify_regime(_candidate(iv_rank=20.0, is_trending=False), LIMITS)

        assert decision.variant is IndexVariant.NO_TRADE
        assert "not trending" in decision.reason

    def test_no_iv_rank_is_unknown_not_no_trade(self) -> None:
        """ "We cannot say" gets fixed by waiting for history. "Sit out" does not."""
        decision = classify_regime(_candidate(iv_rank=None), LIMITS)

        assert decision.variant is IndexVariant.UNKNOWN
        assert not decision.is_tradable


class TestHardGates:
    def test_the_expiry_window_depends_on_the_variant(self) -> None:
        # Credit wants 4-9 DTE; debit wants 10-20. 15 DTE suits only the debit.
        credit = expiry_window(_candidate(iv_rank=80.0, days_to_expiry=15), LIMITS)
        debit = expiry_window(_candidate(iv_rank=20.0, is_trending=True, days_to_expiry=15), LIMITS)

        assert credit.verdict is Verdict.FAIL
        assert debit.verdict is Verdict.PASS

    @pytest.mark.parametrize(
        ("sessions", "expected"),
        [(0, Verdict.FAIL), (1, Verdict.FAIL), (2, Verdict.PASS)],
    )
    def test_no_position_is_opened_into_expiry(self, sessions: int, expected: Verdict) -> None:
        """Expiry-day gamma is how defined-risk stops being defined-risk."""
        result = flat_before_expiry(_candidate(sessions_to_expiry=sessions), LIMITS)
        assert result.verdict is expected

    @pytest.mark.parametrize("event", ["RBI_POLICY", "CPI", "IIP", "FOMC", "UNION_BUDGET"])
    def test_each_macro_event_blocks_entry(self, event: str) -> None:
        result = no_blocking_event(_candidate(events_in_window=(event,)), LIMITS)
        assert result.verdict is Verdict.FAIL

    def test_an_unrelated_event_does_not_block(self) -> None:
        result = no_blocking_event(_candidate(events_in_window=("AGM",)), LIMITS)
        assert result.verdict is Verdict.PASS

    @pytest.mark.parametrize(("open_count", "expected"), [(0, Verdict.PASS), (1, Verdict.FAIL)])
    def test_only_one_index_position_at_a_time(self, open_count: int, expected: Verdict) -> None:
        result = one_index_position(_candidate(open_index_positions=open_count), LIMITS)
        assert result.verdict is expected


class TestCombinator:
    def test_a_clean_credit_candidate_passes(self) -> None:
        assert evaluate(_candidate(), LIMITS).approved

    def test_a_middle_band_candidate_is_blocked_by_the_regime_gate(self) -> None:
        report = evaluate(_candidate(iv_rank=50.0), LIMITS)
        blocker = report.first_blocker()

        assert not report.approved
        assert blocker is not None
        assert blocker.name == "regime"

    def test_every_gate_reports_a_measurement(self) -> None:
        for result in evaluate(_candidate(), LIMITS).results:
            assert result.measured
            assert result.threshold


def test_naked_shorts_stay_disabled_in_config() -> None:
    """FR-5.9. The flag is off and stays off through v1 and v2."""
    assert load_setup_thresholds().allow_naked_shorts is False
