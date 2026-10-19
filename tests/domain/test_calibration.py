"""Calibration, checked against cases whose answer is known by hand first.

`docs/12` is explicit about the order: "verify the maths against a known case
before trusting it on real data". So the first test here is arithmetic anyone
can redo on paper, and only after that do the behavioural tests run.
"""

from __future__ import annotations

import pytest

from trading_desk.domain.calibration import (
    MIN_BIN_SAMPLES,
    Calibration,
    ResolvedIdea,
    calibrate,
    succeeded,
)


def ideas(confidence: float, wins: int, losses: int) -> tuple[ResolvedIdea, ...]:
    return tuple(
        [ResolvedIdea(confidence, True) for _ in range(wins)]
        + [ResolvedIdea(confidence, False) for _ in range(losses)]
    )


class TestTheKnownCaseFromDocs12:
    """70% of 70%-confidence ideas succeeded. Worked out by hand:

    Brier    = 0.7 x (0.7-1)^2 + 0.3 x (0.7-0)^2 = 0.7(0.09) + 0.3(0.49) = 0.21
    baseline = the base rate *is* 0.7, so the baseline is the same 0.21
    reliability = (0.7 - 0.7)^2 = 0, perfectly calibrated
    resolution  = (0.7 - 0.7)^2 = 0, one bin, so it tells you nothing
    uncertainty = 0.7 x 0.3     = 0.21
    and the decomposition must close: 0 - 0 + 0.21 = 0.21
    """

    def setup_method(self) -> None:
        self.result = calibrate(ideas(0.7, wins=70, losses=30))

    def test_the_brier_score_is_the_hand_computed_value(self) -> None:
        assert self.result.brier == pytest.approx(0.21)

    def test_the_baseline_matches_because_the_claim_is_the_base_rate(self) -> None:
        assert self.result.baseline_brier == pytest.approx(0.21)

    def test_reliability_is_zero_because_the_claim_was_exactly_right(self) -> None:
        assert self.result.reliability == pytest.approx(0.0)

    def test_resolution_is_zero_because_one_band_cannot_discriminate(self) -> None:
        assert self.result.resolution == pytest.approx(0.0)

    def test_the_decomposition_closes(self) -> None:
        rebuilt = self.result.reliability - self.result.resolution + self.result.uncertainty
        assert rebuilt == pytest.approx(self.result.brier)

    def test_perfect_calibration_alone_does_not_beat_the_baseline(self) -> None:
        """The finding this whole module exists for.

        An agent that always says 0.7 and is right 70% of the time is perfectly
        calibrated and carries no information. If `beats_baseline` were true
        here, the sizing lock would open on a forecaster that is just reporting
        the base rate in a louder voice.
        """
        assert not self.result.beats_baseline
        assert self.result.skill == pytest.approx(0.0)


class TestDiscrimination:
    def test_a_forecaster_that_separates_winners_from_losers_beats_the_baseline(self) -> None:
        """90%-confidence ideas win 90% of the time, 30%-confidence ones 30%."""
        confident = ideas(0.9, wins=45, losses=5)
        doubtful = ideas(0.3, wins=15, losses=35)
        result = calibrate(confident + doubtful)

        assert result.beats_baseline
        assert result.skill > 0.2
        assert result.resolution > result.reliability

    def test_an_inverted_forecaster_is_worse_than_guessing(self) -> None:
        """High confidence on losers. Skill must be negative, not merely low."""
        result = calibrate(ideas(0.9, wins=5, losses=45) + ideas(0.3, wins=35, losses=15))
        assert not result.beats_baseline
        assert result.skill < 0.0


class TestTheReliabilityCurve:
    def test_every_decile_is_reported_including_the_empty_ones(self) -> None:
        result = calibrate(ideas(0.75, wins=3, losses=1))
        assert len(result.bins) == 10
        assert sum(1 for band in result.bins if band.count) == 1

    def test_a_thin_band_says_it_is_noise_rather_than_printing_a_rate(self) -> None:
        result = calibrate(ideas(0.75, wins=1, losses=1))
        band = next(b for b in result.bins if b.count)
        assert not band.is_meaningful
        assert "noise" in band.describe()
        assert str(MIN_BIN_SAMPLES) in band.describe()

    def test_a_full_band_reports_claimed_observed_and_the_gap(self) -> None:
        result = calibrate(ideas(0.8, wins=5, losses=5))
        band = next(b for b in result.bins if b.count)
        assert band.gap == pytest.approx(0.3)  # claimed 80%, observed 50%
        described = band.describe()
        assert "claimed 80.0%" in described
        assert "observed 50.0%" in described

    def test_a_confidence_of_exactly_one_lands_in_the_top_band(self) -> None:
        """Off-by-one: int(1.0 * 10) is 10, which is not a band index."""
        result = calibrate((ResolvedIdea(1.0, True),))
        assert result.bins[-1].count == 1


class TestRefusals:
    def test_an_empty_set_raises_rather_than_scoring_zero(self) -> None:
        """A Brier of 0.0 is a perfect forecaster, which is what no data must
        never be mistaken for (rule 6)."""
        with pytest.raises(ValueError, match="at least one"):
            calibrate(())

    def test_a_confidence_outside_zero_to_one_is_refused(self) -> None:
        with pytest.raises(ValueError, match="0 to 1"):
            ResolvedIdea(1.4, True)

    def test_skill_is_zero_not_infinite_when_the_baseline_is_perfect(self) -> None:
        """Every idea won, so the base rate of 1.0 predicts perfectly and the
        baseline Brier is 0. Dividing by it would raise."""
        result = calibrate(ideas(1.0, wins=5, losses=0))
        assert result.baseline_brier == pytest.approx(0.0)
        assert result.skill == 0.0


class TestWhatCountsAsSuccess:
    def test_success_is_net_money_and_breakeven_is_not_success(self) -> None:
        assert succeeded(1)
        assert not succeeded(0)
        assert not succeeded(-1)


class TestTheSummaryCarriesNumbers:
    def test_the_summary_names_both_scores_and_the_sample_count(self) -> None:
        result: Calibration = calibrate(ideas(0.7, wins=7, losses=3))
        summary = result.summary()
        assert "10 resolved" in summary
        assert "base rate 70.0%" in summary
        assert "Brier" in summary and "baseline" in summary
        assert "does not beat" in summary
