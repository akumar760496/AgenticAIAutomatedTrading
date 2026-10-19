"""Credibility scoring. Rules, not a model, so the numbers are checkable by hand."""

from __future__ import annotations

from trading_desk.domain.credibility import (
    BASE_BY_TIER,
    HEDGED_CEILING,
    score_credibility,
)
from trading_desk.domain.enums import SourceTier


class TestTheHedgingCeiling:
    """docs/06: "sources say" stories cannot reach Setup A's 0.7 threshold."""

    def test_a_sources_say_story_cannot_reach_the_setup_a_threshold(self) -> None:
        score = score_credibility(
            headline="Company may buy rival, sources say",
            body="People familiar with the matter said talks are under way.",
            source_tier=SourceTier.TIER_1,
        )

        assert score.value < 0.7
        assert score.was_capped is False or score.value <= HEDGED_CEILING

    def test_bonuses_cannot_buy_a_hedged_story_back_over_the_ceiling(self) -> None:
        """The ceiling is applied last. A hedged story with every bonus
        available must still come in under it - otherwise the rule is only a
        penalty, and penalties can be out-earned."""
        score = score_credibility(
            headline="Company may buy rival, sources say",
            body=(
                "People familiar with the matter said talks are under way. "
                "The company said in a statement that it does not comment on "
                "speculation. A regulatory filing was referenced."
            ),
            source_tier=SourceTier.TIER_1,
            corroborating_publishers=10,
        )

        assert score.value <= HEDGED_CEILING
        assert score.was_capped
        assert "ceiling applied" in score.explain()

    def test_an_unhedged_tier_one_story_passes_the_threshold(self) -> None:
        score = score_credibility(
            headline="Company reports 18% profit growth",
            body="The company said in a statement that net profit rose 18%.",
            source_tier=SourceTier.TIER_1,
        )

        assert score.value >= 0.7


class TestExchangeFilings:
    def test_a_filing_scores_one_and_is_not_second_guessed(self) -> None:
        """Nothing about how a filing is worded makes it less true. A filing
        that happens to contain the word 'reportedly' is still the primary
        document."""
        score = score_credibility(
            headline="Board approves acquisition",
            body="The board reportedly approved the scheme of arrangement.",
            source_tier=SourceTier.EXCHANGE,
        )

        assert score.value == 1.0
        assert not score.was_capped


class TestTheScoreShowsItsWorking:
    def test_every_score_lists_the_rules_that_produced_it(self) -> None:
        """docs/12 asks Ajay to read 20 scores and say whether he agrees. He
        cannot do that with a bare number."""
        score = score_credibility(
            headline="Company wins order",
            body="The company said in a statement it won a Rs 500 crore order.",
            source_tier=SourceTier.TIER_2,
            corroborating_publishers=2,
        )

        rule_names = {rule.name for rule in score.rules}
        assert "source tier" in rule_names
        assert "named attribution" in rule_names
        assert "corroboration" in rule_names
        assert "TIER_2" in score.explain()

    def test_the_evidence_names_the_words_that_triggered_the_rule(self) -> None:
        score = score_credibility(
            headline="Deal talks reported",
            body="Sources say the two firms are in discussions.",
            source_tier=SourceTier.TIER_2,
        )

        hedging = [rule for rule in score.rules if rule.name == "hedged attribution"]
        assert hedging and "sources say" in hedging[0].evidence


class TestCorroboration:
    def test_more_outlets_raise_the_score_but_not_without_limit(self) -> None:
        one = score_credibility("Order win", "The company won an order.", SourceTier.TIER_2, 1)
        many = score_credibility("Order win", "The company won an order.", SourceTier.TIER_2, 20)

        assert many.value > one.value
        assert many.value - BASE_BY_TIER[SourceTier.TIER_2] <= 0.15 + 1e-9

    def test_a_lone_story_gets_no_corroboration_rule_at_all(self) -> None:
        score = score_credibility("Order win", "The company won an order.", SourceTier.TIER_2, 0)

        assert "corroboration" not in {rule.name for rule in score.rules}


def test_a_score_never_leaves_the_zero_to_one_range() -> None:
    worst = score_credibility(
        headline="Unconfirmed rumour",
        body="Rumours, speculation, unconfirmed reports, sources say, could not be verified.",
        source_tier=SourceTier.UNVERIFIED,
    )

    assert 0.0 <= worst.value <= 1.0
