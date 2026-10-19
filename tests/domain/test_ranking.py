"""Ranking to at most two, and the tie-break that makes it repeatable."""

from __future__ import annotations

import pytest

from trading_desk.domain.enums import Direction, SetupId
from trading_desk.domain.ranking import (
    UNCALIBRATED_CONFIDENCE_CEILING,
    Candidate,
    rank_candidates,
)


def _candidate(symbol: str, credibility: float = 0.8, reward: float = 2.0) -> Candidate:
    return Candidate(
        symbol=symbol,
        setup_id=SetupId.CATALYST_CONTINUATION,
        direction=Direction.BULLISH,
        credibility=credibility,
        reward_to_risk=reward,
    )


class TestOrdering:
    def test_the_more_credible_idea_ranks_first(self) -> None:
        ranked, _ = rank_candidates([_candidate("A", 0.7), _candidate("B", 0.9)])

        assert [item.candidate.symbol for item in ranked] == ["B", "A"]
        assert [item.rank for item in ranked] == [1, 2]

    def test_reward_to_risk_breaks_a_credibility_tie(self) -> None:
        ranked, _ = rank_candidates(
            [_candidate("A", 0.8, reward=1.5), _candidate("B", 0.8, reward=3.0)]
        )

        assert [item.candidate.symbol for item in ranked] == ["B", "A"]

    def test_the_symbol_breaks_a_total_tie(self) -> None:
        """Without this the order depends on input order, and the determinism
        check in docs/12 fails for a reason unrelated to the maths."""
        ranked, _ = rank_candidates([_candidate("ZEEL"), _candidate("ACC")])

        assert [item.candidate.symbol for item in ranked] == ["ACC", "ZEEL"]

    def test_the_order_does_not_depend_on_input_order(self) -> None:
        forwards, _ = rank_candidates([_candidate("ACC"), _candidate("ZEEL")])
        backwards, _ = rank_candidates([_candidate("ZEEL"), _candidate("ACC")])

        assert [item.candidate.symbol for item in forwards] == [
            item.candidate.symbol for item in backwards
        ]


class TestTheCeiling:
    def test_at_most_two_are_ranked(self) -> None:
        ranked, overflow = rank_candidates(
            [_candidate("A", 0.9), _candidate("B", 0.8), _candidate("C", 0.7)]
        )

        assert len(ranked) == 2
        assert [item.symbol for item in overflow] == ["C"]

    def test_the_overflow_is_returned_rather_than_dropped(self) -> None:
        """An idea that passed every gate and lost on rank is a different
        thing from an idea that was killed, and the briefing shows both."""
        _, overflow = rank_candidates([_candidate("A"), _candidate("B"), _candidate("C")])

        assert overflow

    def test_no_candidates_ranks_nothing_without_complaining(self) -> None:
        ranked, overflow = rank_candidates([])

        assert ranked == []
        assert overflow == []


class TestConfidence:
    def test_confidence_is_capped_until_calibration_exists(self) -> None:
        assert _candidate("A", credibility=0.95).confidence() == UNCALIBRATED_CONFIDENCE_CEILING

    def test_a_low_credibility_is_not_inflated_to_the_ceiling(self) -> None:
        assert _candidate("A", credibility=0.3).confidence() == pytest.approx(0.3)

    def test_reward_to_risk_does_not_raise_confidence(self) -> None:
        """A wider spread pays better; it is not more likely to happen."""
        narrow = _candidate("A", credibility=0.4, reward=1.0).confidence()
        wide = _candidate("A", credibility=0.4, reward=9.0).confidence()

        assert narrow == wide


class TestRejectedInput:
    def test_a_credibility_outside_zero_to_one_raises(self) -> None:
        with pytest.raises(ValueError, match="credibility"):
            _candidate("A", credibility=1.4)

    def test_a_negative_reward_to_risk_raises(self) -> None:
        with pytest.raises(ValueError, match="reward_to_risk"):
            _candidate("A", reward=-1.0)
