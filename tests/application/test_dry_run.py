"""The dry run, and the lookahead guard that makes it worth anything."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from tests.fakes.bars import bars_from_closes
from trading_desk.application.dry_run import DryRun, LookaheadError, PointInTimeView
from trading_desk.domain.enums import Direction
from trading_desk.domain.setups.setup_a import LegLiquidity, SetupACandidate
from trading_desk.domain.setups.thresholds import load_setup_thresholds
from trading_desk.domain.values import Money

LIMITS = load_setup_thresholds().setup_a
AS_OF = datetime(2026, 1, 10, tzinfo=UTC)


def _leg() -> LegLiquidity:
    return LegLiquidity(
        open_interest_lots=1000,
        bid=Money.from_rupees(Decimal("99")),
        ask=Money.from_rupees(Decimal("101")),
    )


def _candidate(symbol: str = "RELIANCE", **overrides: object) -> SetupACandidate:
    base = {
        "instrument_symbol": symbol,
        "direction": Direction.BULLISH,
        "sessions_since_catalyst": 1,
        "catalyst_type": "LARGE_ORDER_WIN",
        "credibility": 0.85,
        "move_since_catalyst": Money.from_rupees(Decimal("10")),
        "atr": Money.from_rupees(Decimal("20")),
        "close_beyond_range_edge": True,
        "volume_ratio": 2.0,
        "iv_rank": 40.0,
        "legs": (_leg(), _leg()),
        "days_to_expiry": 15,
    }
    base.update(overrides)
    return SetupACandidate(**base)  # type: ignore[arg-type]


class TestPointInTime:
    def test_only_bars_up_to_the_decision_moment_are_visible(self) -> None:
        bars = bars_from_closes([Decimal("100")] * 30)
        view = PointInTimeView(bars=tuple(bars), as_of=AS_OF)

        visible = view.visible()
        assert all(bar.start <= AS_OF for bar in visible)
        assert len(visible) < len(bars)

    def test_injected_future_data_is_detected(self) -> None:
        """FR-10.4. The guard is driven to fire, not merely present - a
        lookahead that nothing tests for is the one you ship."""
        bars = bars_from_closes([Decimal("100")] * 5)
        view = PointInTimeView(bars=tuple(bars), as_of=bars[0].start - timedelta(days=1))

        with pytest.raises(LookaheadError, match="postdate the decision moment"):
            view.require_no_future()

    def test_a_clean_window_does_not_raise(self) -> None:
        bars = bars_from_closes([Decimal("100")] * 5)
        view = PointInTimeView(bars=tuple(bars), as_of=bars[-1].start)

        view.require_no_future()  # must not raise

    def test_a_naive_as_of_is_refused(self) -> None:
        with pytest.raises(ValueError, match="tz-aware"):
            PointInTimeView(bars=(), as_of=datetime(2026, 1, 10))  # noqa: DTZ001


class TestDryRun:
    def test_a_passing_candidate_is_recorded_as_would_have_fired(self) -> None:
        report = DryRun(LIMITS).evaluate_candidates([(date(2026, 1, 10), _candidate())])

        assert len(report.would_have_fired) == 1
        assert report.fire_rate == 1.0

    def test_blockers_are_counted_so_the_killing_gate_is_visible(self) -> None:
        """The question the dry run exists to answer: which gate is doing the
        killing? Counting only pass/fail cannot answer it."""
        candidates = [
            (date(2026, 1, 10), _candidate("A", iv_rank=90.0)),
            (date(2026, 1, 10), _candidate("B", iv_rank=95.0)),
            (date(2026, 1, 11), _candidate("C", credibility=0.1)),
        ]

        report = DryRun(LIMITS).evaluate_candidates(candidates)

        assert report.blocked_by["iv_rank"] == 2
        assert report.blocked_by["credibility"] == 1
        assert report.days_evaluated == 2

    def test_an_empty_run_reports_honestly_rather_than_zero_percent(self) -> None:
        report = DryRun(LIMITS).evaluate_candidates([])

        assert report.fire_rate == 0.0
        assert "not enough history" in report.summary()

    def test_the_summary_names_the_top_blockers(self) -> None:
        candidates = [(date(2026, 1, 10), _candidate(f"S{i}", iv_rank=90.0)) for i in range(5)]

        report = DryRun(LIMITS).evaluate_candidates(candidates)

        assert "iv_rank x5" in report.summary()
        assert "0.0%" in report.summary()
