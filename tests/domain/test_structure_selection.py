"""The volatility matrix: which structure, and when none of them.

The test that earns its place is the middle band. It is the cell everyone
forgets, and forgetting it means the system always has an answer - which is the
failure mode `docs/02` warns about in a different context: an agent that finds
an idea every day is pleasing you, not analysing.
"""

from __future__ import annotations

from datetime import time

import pytest

from trading_desk.domain.enums import Direction
from trading_desk.domain.options.selection import (
    NO_SHORT_GAMMA_AFTER,
    StructureKind,
    choose_structure,
)

# Setup B's configured bands.
CREDIT_ABOVE = 70.0
DEBIT_BELOW = 35.0


def _choose(iv_rank: float | None, **overrides: object) -> StructureKind:
    arguments: dict[str, object] = {
        "iv_rank": iv_rank,
        "direction": Direction.BULLISH,
        "credit_above": CREDIT_ABOVE,
        "debit_below": DEBIT_BELOW,
    }
    arguments.update(overrides)
    return choose_structure(**arguments).kind  # type: ignore[arg-type]


class TestTheThreeBands:
    @pytest.mark.parametrize("iv_rank", [70.0, 85.0, 100.0])
    def test_rich_premium_is_sold(self, iv_rank: float) -> None:
        assert _choose(iv_rank) is StructureKind.CREDIT_VERTICAL

    @pytest.mark.parametrize("iv_rank", [0.0, 20.0, 35.0])
    def test_cheap_premium_is_bought(self, iv_rank: float) -> None:
        assert _choose(iv_rank) is StructureKind.DEBIT_VERTICAL

    @pytest.mark.parametrize("iv_rank", [35.1, 50.0, 69.9])
    def test_the_middle_band_trades_nothing(self, iv_rank: float) -> None:
        assert _choose(iv_rank) is StructureKind.NONE

    def test_the_middle_band_says_why(self) -> None:
        choice = choose_structure(
            iv_rank=50.0,
            direction=Direction.BULLISH,
            credit_above=CREDIT_ABOVE,
            debit_below=DEBIT_BELOW,
        )

        assert "between 35 and 70" in choice.reason


class TestRefusals:
    def test_an_unknown_iv_rank_trades_nothing(self) -> None:
        """Rule 6. There is no volatility view, so there is no structure."""
        assert _choose(None) is StructureKind.NONE

    def test_a_neutral_direction_trades_nothing(self) -> None:
        assert _choose(20.0, direction=Direction.NEUTRAL) is StructureKind.NONE


class TestSetupAHasNoCreditAnswer:
    """config/setups.yaml gives Setup A one number, max_iv_rank: 60, and it is
    a ceiling on buying. Passing credit_above=None is how that is said."""

    def test_below_the_ceiling_it_buys(self) -> None:
        choice = choose_structure(
            iv_rank=40.0, direction=Direction.BULLISH, credit_above=None, debit_below=60.0
        )
        assert choice.kind is StructureKind.DEBIT_VERTICAL

    def test_above_the_ceiling_it_does_not_flip_to_selling(self) -> None:
        choice = choose_structure(
            iv_rank=95.0, direction=Direction.BULLISH, credit_above=None, debit_below=60.0
        )

        assert choice.kind is StructureKind.NONE
        assert "does not sell" in choice.reason


class TestExpiryDayShortGamma:
    def test_a_credit_spread_is_refused_late_on_expiry_day(self) -> None:
        choice = choose_structure(
            iv_rank=90.0,
            direction=Direction.BULLISH,
            credit_above=CREDIT_ABOVE,
            debit_below=DEBIT_BELOW,
            is_expiry_day=True,
            time_of_day=time(14, 45),
        )

        assert choice.kind is StructureKind.NONE
        assert "short gamma" in choice.reason

    def test_the_cutoff_itself_is_already_too_late(self) -> None:
        choice = choose_structure(
            iv_rank=90.0,
            direction=Direction.BULLISH,
            credit_above=CREDIT_ABOVE,
            debit_below=DEBIT_BELOW,
            is_expiry_day=True,
            time_of_day=NO_SHORT_GAMMA_AFTER,
        )
        assert choice.kind is StructureKind.NONE

    def test_earlier_on_expiry_day_it_is_allowed(self) -> None:
        choice = choose_structure(
            iv_rank=90.0,
            direction=Direction.BULLISH,
            credit_above=CREDIT_ABOVE,
            debit_below=DEBIT_BELOW,
            is_expiry_day=True,
            time_of_day=time(10, 0),
        )
        assert choice.kind is StructureKind.CREDIT_VERTICAL

    def test_an_unknown_time_on_expiry_day_counts_as_too_late(self) -> None:
        """Fail closed: not knowing the time is not the same as it being early."""
        choice = choose_structure(
            iv_rank=90.0,
            direction=Direction.BULLISH,
            credit_above=CREDIT_ABOVE,
            debit_below=DEBIT_BELOW,
            is_expiry_day=True,
            time_of_day=None,
        )
        assert choice.kind is StructureKind.NONE

    def test_a_debit_spread_is_unaffected_by_expiry_afternoon(self) -> None:
        """It is long gamma, so the rule does not apply to it."""
        choice = choose_structure(
            iv_rank=10.0,
            direction=Direction.BULLISH,
            credit_above=CREDIT_ABOVE,
            debit_below=DEBIT_BELOW,
            is_expiry_day=True,
            time_of_day=time(15, 20),
        )
        assert choice.kind is StructureKind.DEBIT_VERTICAL
