"""Catching a number the model invented."""

from __future__ import annotations

from trading_desk.domain.grounding import numbers_in, ungrounded_numbers

FACTS = "Gate iv_rank: 40.0 (proxy)\nOptions max_loss_per_unit: ₹22.00\nOrder worth Rs 4,500 crore"


class TestFindingNumbers:
    def test_commas_are_stripped_so_the_same_figure_compares_equal(self) -> None:
        assert numbers_in("Rs 4,500 crore") == ["4500"]

    def test_decimals_are_kept_whole(self) -> None:
        assert numbers_in("iv rank 40.5") == ["40.5"]

    def test_text_with_no_numbers_has_none(self) -> None:
        assert numbers_in("the order was large") == []


class TestGrounding:
    def test_a_figure_from_the_facts_passes(self) -> None:
        assert ungrounded_numbers("The Rs 4,500 crore order is large.", FACTS) == []

    def test_a_figure_from_nowhere_is_caught(self) -> None:
        assert ungrounded_numbers("Profit should rise 37%.", FACTS) == ["37"]

    def test_the_same_figure_written_differently_still_passes(self) -> None:
        """A model that reformats 4,500 as 4500 has not invented anything."""
        assert ungrounded_numbers("the 4500 crore order", FACTS) == []

    def test_prose_with_no_numbers_is_always_grounded(self) -> None:
        assert ungrounded_numbers("A large order win adds visible revenue.", FACTS) == []

    def test_every_invented_number_is_named_not_just_the_first(self) -> None:
        found = ungrounded_numbers("Up 37% on 12 contracts.", FACTS)

        assert found == ["37", "12"]
