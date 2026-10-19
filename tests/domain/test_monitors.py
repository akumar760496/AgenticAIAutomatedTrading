"""The no-trade band and the engagement metric.

The test worth reading twice is
`test_a_system_that_briefs_every_single_day_is_reported_as_too_loose`. That is
the dangerous direction: it looks like a productive system and means the gates
are approving whatever they see.
"""

from __future__ import annotations

import pytest

from trading_desk.domain.monitors import (
    MIN_SESSIONS_FOR_RATE,
    Engagement,
    NoTradeRate,
)


class TestTheNoTradeBand:
    def test_a_rate_inside_the_band_is_reported_as_healthy(self) -> None:
        rate = NoTradeRate(sessions=20, sessions_with_no_idea=10)  # 50%
        assert not rate.needs_alert
        assert "inside the band" in rate.describe()

    def test_a_system_that_briefs_every_single_day_is_reported_as_too_loose(self) -> None:
        """0% quiet. docs/12 section 6: "gates fire on almost every candidate -
        a gate reads a field that's always null, so it always passes"."""
        rate = NoTradeRate(sessions=20, sessions_with_no_idea=0)
        assert rate.is_too_loose
        assert rate.needs_alert
        assert "TOO LOOSE" in rate.describe()

    def test_a_system_that_has_gone_quiet_is_reported_as_too_quiet(self) -> None:
        """A dead feed looks exactly like a slow news month (ISS-016)."""
        rate = NoTradeRate(sessions=20, sessions_with_no_idea=18)  # 90%
        assert rate.is_too_quiet
        assert rate.needs_alert
        assert "TOO QUIET" in rate.describe()
        assert "check the feeds before the thresholds" in rate.describe()

    def test_the_synthetic_run_docs_12_asks_for_fires_the_alert(self) -> None:
        """docs/12: "The no-trade monitor alerts on a synthetic run below 20%"."""
        rate = NoTradeRate(sessions=50, sessions_with_no_idea=9)  # 18%
        assert rate.rate is not None
        assert rate.rate < 0.20
        assert rate.needs_alert

    def test_both_edges_of_the_band_are_inclusive_and_quiet(self) -> None:
        assert not NoTradeRate(sessions=10, sessions_with_no_idea=4).needs_alert  # 40%
        assert not NoTradeRate(sessions=10, sessions_with_no_idea=6).needs_alert  # 60%
        assert NoTradeRate(sessions=100, sessions_with_no_idea=39).needs_alert  # 39%
        assert NoTradeRate(sessions=100, sessions_with_no_idea=61).needs_alert  # 61%


class TestNotEnoughDataIsNotAVerdict:
    def test_three_quiet_days_out_of_four_does_not_raise_an_alarm(self) -> None:
        """75% looks far outside the band and is four sessions of noise."""
        rate = NoTradeRate(sessions=4, sessions_with_no_idea=3)
        assert not rate.is_measurable
        assert not rate.needs_alert
        assert f"under {MIN_SESSIONS_FOR_RATE} sessions" in rate.describe()

    def test_no_sessions_gives_no_rate_rather_than_zero_percent(self) -> None:
        """A rate of 0.0 is "briefs every day", which is the opposite of "has
        never run" (rule 6)."""
        rate = NoTradeRate(sessions=0, sessions_with_no_idea=0)
        assert rate.rate is None
        assert not rate.needs_alert
        assert "no sessions recorded yet" in rate.describe()

    def test_more_quiet_sessions_than_sessions_is_refused(self) -> None:
        with pytest.raises(ValueError, match="is not possible"):
            NoTradeRate(sessions=3, sessions_with_no_idea=4)


class TestEngagement:
    def test_a_fully_answered_set_reads_as_full_engagement(self) -> None:
        engagement = Engagement(briefings_sent=10, briefings_answered=10)
        assert engagement.answered_fraction == pytest.approx(1.0)
        assert not engagement.fails_gate_two

    def test_mostly_ignored_briefings_fail_docs_06_gate_two(self) -> None:
        engagement = Engagement(briefings_sent=10, briefings_answered=2)  # 80% ignored
        assert engagement.fails_gate_two
        assert "Gate 2" in engagement.describe()

    def test_exactly_seventy_percent_ignored_does_not_yet_fail(self) -> None:
        """The criterion is ">70% ignored", so 70% is not it."""
        assert not Engagement(briefings_sent=10, briefings_answered=3).fails_gate_two
        assert Engagement(briefings_sent=100, briefings_answered=29).fails_gate_two

    def test_nothing_sent_is_not_a_failure_to_engage(self) -> None:
        engagement = Engagement(briefings_sent=0, briefings_answered=0)
        assert engagement.answered_fraction is None
        assert not engagement.fails_gate_two
        assert "no briefings sent yet" in engagement.describe()

    def test_more_answered_than_sent_is_refused(self) -> None:
        with pytest.raises(ValueError, match="is not possible"):
            Engagement(briefings_sent=2, briefings_answered=3)

    def test_the_description_carries_both_counts(self) -> None:
        described = Engagement(briefings_sent=10, briefings_answered=7).describe()
        assert "70%" in described
        assert "7 of 10" in described
