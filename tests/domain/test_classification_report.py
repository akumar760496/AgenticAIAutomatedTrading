"""The confusion matrix, and why accuracy alone is not allowed to be the answer."""

from __future__ import annotations

from trading_desk.domain.classification_report import (
    HOLDOUT_FRACTION,
    build_report,
    is_held_out,
)

CLASSES = ("EARNINGS_SURPRISE", "CREDIT_RATING_ACTION", "NONE")


class TestAccuracyHidesTheRareClasses:
    def test_a_classifier_that_always_says_none_scores_well_and_is_useless(self) -> None:
        """The exact failure docs/12 warns about. 90% accurate, and it has
        never once identified a catalyst."""
        pairs = [("NONE", "NONE")] * 90
        pairs += [("EARNINGS_SURPRISE", "NONE")] * 8
        pairs += [("CREDIT_RATING_ACTION", "NONE")] * 2

        report = build_report(pairs, CLASSES)

        assert report.accuracy == 0.90
        assert report.never_predicted_classes() == [
            "EARNINGS_SURPRISE",
            "CREDIT_RATING_ACTION",
        ]

    def test_the_printed_report_says_so_in_capitals(self) -> None:
        pairs = [("NONE", "NONE")] * 9 + [("CREDIT_RATING_ACTION", "NONE")]

        lines = "\n".join(build_report(pairs, CLASSES).as_lines())

        assert "NEVER PREDICTED" in lines
        assert "CREDIT_RATING_ACTION" in lines

    def test_a_class_absent_from_the_data_still_appears_as_a_row(self) -> None:
        """Inferring the class list from the data makes a missing category
        invisible, which is the failure this report exists to surface."""
        report = build_report([("NONE", "NONE")], CLASSES)

        assert len(report.per_class()) == len(CLASSES)


class TestPerClassNumbers:
    def test_recall_and_precision_are_different_and_both_reported(self) -> None:
        pairs = [
            ("EARNINGS_SURPRISE", "EARNINGS_SURPRISE"),
            ("EARNINGS_SURPRISE", "NONE"),
            ("NONE", "EARNINGS_SURPRISE"),
        ]
        earnings = build_report(pairs, CLASSES).performance("EARNINGS_SURPRISE")

        assert earnings.recall == 0.5  # caught 1 of the 2 that really were
        assert earnings.precision == 0.5  # of 2 it claimed, 1 was right

    def test_the_weakest_class_ignores_classes_with_no_examples(self) -> None:
        pairs = [("NONE", "NONE")] * 5 + [("EARNINGS_SURPRISE", "NONE")]

        weakest = build_report(pairs, CLASSES).weakest_class()

        assert weakest is not None
        assert weakest.label == "EARNINGS_SURPRISE"

    def test_an_empty_report_has_no_weakest_class_rather_than_crashing(self) -> None:
        assert build_report([], CLASSES).weakest_class() is None
        assert build_report([], CLASSES).accuracy == 0.0


class TestTheHeldOutSplit:
    def test_an_item_stays_on_the_same_side_for_ever(self) -> None:
        """A seeded shuffle is reproducible only until the sample size changes.
        Hashing the id means a held-out item can never drift into tuning."""
        assert is_held_out("item-42") == is_held_out("item-42")

    def test_the_split_is_roughly_the_intended_fraction(self) -> None:
        ids = [f"article-{index}" for index in range(2000)]
        held = sum(1 for item in ids if is_held_out(item))

        assert abs(held / len(ids) - HOLDOUT_FRACTION) < 0.03

    def test_adding_items_does_not_move_the_existing_ones(self) -> None:
        """The property that makes "never looked at while tuning" enforceable."""
        before = {item: is_held_out(item) for item in (f"a-{i}" for i in range(50))}
        after = {item: is_held_out(item) for item in (f"a-{i}" for i in range(500))}

        assert all(after[item] == verdict for item, verdict in before.items())
