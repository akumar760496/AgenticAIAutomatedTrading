"""One event, many sources."""

from __future__ import annotations

from trading_desk.domain.dedup import Document, content_hash, group_identical


class TestContentHash:
    def test_identical_content_hashes_the_same(self) -> None:
        assert content_hash("Headline", "Body") == content_hash("Headline", "Body")

    def test_case_and_whitespace_do_not_matter(self) -> None:
        assert content_hash("Head Line", "The  body") == content_hash("HEAD   LINE", "the body")

    def test_different_bodies_hash_differently(self) -> None:
        assert content_hash("H", "will proceed") != content_hash("H", "will not proceed")

    def test_a_negation_is_never_normalised_away(self) -> None:
        """ "will not proceed" against "will now proceed" is two opposite trades."""
        assert content_hash("H", "will not proceed") != content_hash("H", "will now proceed")

    def test_the_headline_body_split_cannot_collide(self) -> None:
        assert content_hash("AB", "C") != content_hash("A", "BC")


class TestGrouping:
    def test_syndicated_copies_become_one_event(self) -> None:
        documents = [
            Document(f"outlet-{index}", "Acme wins order", "Acme has won a large order.")
            for index in range(5)
        ]

        events = group_identical(documents)

        assert len(events) == 1
        assert events[0].source_count == 5

    def test_distinct_stories_stay_distinct(self) -> None:
        documents = [
            Document("a", "Acme wins order", "Acme has won a large order."),
            Document("b", "Acme loses order", "Acme has lost a large order."),
        ]

        assert len(group_identical(documents)) == 2

    def test_the_event_key_is_stable_across_runs(self) -> None:
        """So the same story ingested tomorrow lands on the same key with no state."""
        first = group_identical([Document("a", "H", "B")])
        later = group_identical([Document("b", "H", "B")])

        assert first[0].event_key == later[0].event_key

    def test_every_source_is_listed(self) -> None:
        documents = [
            Document("a", "H", "B"),
            Document("b", "H", "B"),
            Document("c", "Other", "Different"),
        ]

        events = group_identical(documents)
        listed = {source for event in events for source in event.source_ids}

        assert listed == {"a", "b", "c"}

    def test_empty_input(self) -> None:
        assert group_identical([]) == []
