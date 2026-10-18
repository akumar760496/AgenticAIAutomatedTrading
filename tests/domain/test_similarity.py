"""Near-duplicate grouping, and why text similarity alone is unsafe."""

from __future__ import annotations

import pytest

from trading_desk.domain.similarity import (
    DEFAULT_THRESHOLD,
    SimilarDocument,
    group_near_duplicates,
    shingles,
    similarity,
)

INFY = frozenset({"INFY"})
TCS = frozenset({"TCS"})


class TestSimilarity:
    def test_identical_text_scores_one(self) -> None:
        assert similarity("Infosys Q2 profit rises", "Infosys Q2 profit rises") == 1.0

    def test_unrelated_text_scores_near_zero(self) -> None:
        score = similarity("Infosys Q2 profit rises 12%", "Coal India output falls 5% in September")

        assert score < 0.1

    def test_case_and_punctuation_do_not_matter(self) -> None:
        assert similarity("HDFC Bank, raises rates!", "hdfc bank raises rates") == 1.0

    def test_empty_text_scores_zero_rather_than_raising(self) -> None:
        assert similarity("", "Infosys Q2 profit") == 0.0

    def test_a_reworded_headline_still_scores_above_the_threshold(self) -> None:
        score = similarity(
            "Infosys Q2 profit rises 12% beating estimates",
            "Infosys Q2 profit climbs 12%, ahead of estimates",
        )

        assert score >= DEFAULT_THRESHOLD


class TestShingles:
    def test_short_text_becomes_a_single_shingle(self) -> None:
        assert shingles("ab") == frozenset({"ab"})

    def test_empty_text_has_no_shingles(self) -> None:
        assert shingles("") == frozenset()


class TestPartitioning:
    def test_different_instruments_never_merge_however_similar(self) -> None:
        """The case that makes partitioning mandatory. These two headlines score
        0.84 on text alone, and merging them would attribute a TCS contract to
        Infosys."""
        documents = [
            SimilarDocument("a", "TCS wins $1 billion deal with European retailer", TCS),
            SimilarDocument("b", "Infosys wins $1 billion deal with European retailer", INFY),
        ]

        assert similarity(documents[0].text, documents[1].text) > 0.7
        assert len(group_near_duplicates(documents)) == 2

    def test_the_same_story_about_one_instrument_merges(self) -> None:
        documents = [
            SimilarDocument("et", "Infosys Q2 profit rises 12% beating estimates", INFY),
            SimilarDocument("mint", "Infosys Q2 profit climbs 12%, ahead of estimates", INFY),
        ]

        groups = group_near_duplicates(documents)

        assert len(groups) == 1
        assert groups[0].size == 2
        assert groups[0].is_syndicated

    def test_different_events_for_one_instrument_stay_apart(self) -> None:
        documents = [
            SimilarDocument("a", "Infosys Q2 profit rises 12% beating estimates", INFY),
            SimilarDocument("b", "Infosys announces $2 billion share buyback", INFY),
        ]

        assert len(group_near_duplicates(documents)) == 2

    def test_unlinked_articles_form_their_own_partition(self) -> None:
        documents = [
            SimilarDocument("a", "Markets close higher on global cues", frozenset()),
            SimilarDocument("b", "Markets end higher on global cues", frozenset()),
            SimilarDocument("c", "Infosys Q2 profit rises", INFY),
        ]

        groups = group_near_duplicates(documents)

        assert len(groups) == 2


class TestGrouping:
    def test_five_outlets_become_one_event_with_five_sources(self) -> None:
        """FR-3.3, stated in the build plan's done-when."""
        base = "HDFC Bank Q2 net profit rises 5% to Rs 19,510 crore"
        variants = [
            base,
            "HDFC Bank Q2 net profit rises 5 percent to Rs 19,510 crore",
            "HDFC Bank Q2: net profit up 5% at Rs 19,510 crore",
            "HDFC Bank Q2 net profit climbs 5% to Rs 19,510 crore",
            "HDFC Bank Q2 profit rises 5% to Rs 19,510 crore, NII grows",
        ]
        documents = [
            SimilarDocument(f"outlet-{index}", text, frozenset({"HDFCBANK"}))
            for index, text in enumerate(variants)
        ]

        groups = group_near_duplicates(documents)

        assert len(groups) == 1
        assert groups[0].size == 5

    def test_a_singleton_reports_no_match_rather_than_a_perfect_one(self) -> None:
        """1.0 for a lone article reads as a perfect duplicate detection when in
        fact nothing was detected."""
        groups = group_near_duplicates([SimilarDocument("a", "Only story", INFY)])

        assert groups[0].closest_match is None
        assert not groups[0].is_syndicated

    def test_the_representative_is_the_first_seen(self) -> None:
        documents = [
            SimilarDocument("first", "Infosys Q2 profit rises 12% beating estimates", INFY),
            SimilarDocument("second", "Infosys Q2 profit climbs 12%, ahead of estimates", INFY),
        ]

        assert group_near_duplicates(documents)[0].representative_id == "first"

    def test_output_follows_input_order(self) -> None:
        documents = [
            SimilarDocument("a", "Coal India output falls", frozenset({"COALINDIA"})),
            SimilarDocument("b", "Infosys Q2 profit rises", INFY),
        ]

        assert [group.representative_id for group in group_near_duplicates(documents)] == [
            "a",
            "b",
        ]

    def test_every_document_appears_in_exactly_one_group(self) -> None:
        documents = [
            SimilarDocument("a", "Infosys Q2 profit rises 12% beating estimates", INFY),
            SimilarDocument("b", "Infosys Q2 profit climbs 12%, ahead of estimates", INFY),
            SimilarDocument("c", "TCS wins a deal", TCS),
        ]

        members = [m for group in group_near_duplicates(documents) for m in group.member_ids]

        assert sorted(members) == ["a", "b", "c"]

    def test_an_invalid_threshold_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="between 0 and 1"):
            group_near_duplicates([], threshold=1.5)

    def test_empty_input(self) -> None:
        assert group_near_duplicates([]) == []
