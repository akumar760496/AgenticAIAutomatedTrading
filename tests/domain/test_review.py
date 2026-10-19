"""Where an idea stands at the close.

The test that earns its place is the bearish one. Every comparison here is
direction-sensitive, and a direction-blind implementation passes all four
bullish cases and silently never fires on a bearish idea.
"""

from __future__ import annotations

from datetime import date

from trading_desk.domain.enums import Direction
from trading_desk.domain.review import IdeaStanding, standing_for
from trading_desk.domain.values import Price

TODAY = date(2026, 10, 19)
EXPIRY = date(2026, 11, 5)


def _standing(
    direction: Direction,
    close: str,
    *,
    stop: str = "1400",
    target: str = "1440",
    expiry: date | None = EXPIRY,
    today: date = TODAY,
) -> IdeaStanding:
    return standing_for(
        direction=direction,
        close=Price.from_rupees(close),
        stop=Price.from_rupees(stop),
        target=Price.from_rupees(target),
        expiry=expiry,
        today=today,
    ).standing


class TestABullishIdea:
    def test_at_or_above_the_target_is_past_target(self) -> None:
        assert _standing(Direction.BULLISH, "1440") is IdeaStanding.PAST_TARGET
        assert _standing(Direction.BULLISH, "1455") is IdeaStanding.PAST_TARGET

    def test_at_or_below_the_stop_is_past_stop(self) -> None:
        assert _standing(Direction.BULLISH, "1400") is IdeaStanding.PAST_STOP
        assert _standing(Direction.BULLISH, "1380") is IdeaStanding.PAST_STOP

    def test_between_them_is_still_running(self) -> None:
        assert _standing(Direction.BULLISH, "1420") is IdeaStanding.INSIDE_LEVELS


class TestABearishIdea:
    """The levels are the other way round, and the comparisons must be too."""

    def test_falling_to_the_target_is_past_target_not_past_stop(self) -> None:
        standing = _standing(Direction.BEARISH, "1400", stop="1440", target="1400")

        assert standing is IdeaStanding.PAST_TARGET

    def test_rising_to_the_stop_is_past_stop(self) -> None:
        standing = _standing(Direction.BEARISH, "1445", stop="1440", target="1400")

        assert standing is IdeaStanding.PAST_STOP

    def test_a_direction_blind_comparison_would_fail_here(self) -> None:
        """The induced version of the bug.

        A bearish idea whose underlying has collapsed well past its target.
        `close >= target` - the bullish test - is False, so a direction-blind
        implementation reports INSIDE_LEVELS: the idea worked perfectly and
        the review says nothing happened.
        """
        standing = _standing(Direction.BEARISH, "1300", stop="1440", target="1400")

        assert standing is IdeaStanding.PAST_TARGET


class TestWhatItRefusesToAnswer:
    def test_no_bar_today_is_unknown_not_a_guess(self) -> None:
        result = standing_for(
            direction=Direction.BULLISH,
            close=None,
            stop=Price.from_rupees("1400"),
            target=Price.from_rupees("1440"),
            expiry=EXPIRY,
            today=TODAY,
        )

        assert result.standing is IdeaStanding.UNKNOWN
        assert "no daily bar" in result.detail

    def test_an_idea_with_no_recorded_levels_is_unknown(self) -> None:
        """Every idea briefed before migration 0008 looks like this."""
        result = standing_for(
            direction=Direction.BULLISH,
            close=Price.from_rupees("1420"),
            stop=None,
            target=None,
            expiry=None,
            today=TODAY,
        )

        assert result.standing is IdeaStanding.UNKNOWN
        assert "no stop/target recorded" in result.detail

    def test_inverted_levels_are_refused_rather_than_resolved_one_way(self) -> None:
        """Both branches would fire. Picking one silently is the bug."""
        result = standing_for(
            direction=Direction.BULLISH,
            close=Price.from_rupees("1420"),
            stop=Price.from_rupees("1440"),
            target=Price.from_rupees("1400"),
            expiry=EXPIRY,
            today=TODAY,
        )

        assert result.standing is IdeaStanding.UNKNOWN
        assert "levels look wrong" in result.detail


class TestExpiry:
    def test_after_expiry_the_standing_is_expired(self) -> None:
        standing = _standing(Direction.BULLISH, "1420", today=date(2026, 11, 6))

        assert standing is IdeaStanding.EXPIRED

    def test_on_expiry_day_itself_the_levels_still_decide(self) -> None:
        """An idea that hits its target on expiry morning reached its target."""
        standing = _standing(Direction.BULLISH, "1450", today=EXPIRY)

        assert standing is IdeaStanding.PAST_TARGET

    def test_every_standing_reports_the_numbers_it_compared(self) -> None:
        """Rule 9: a comparison says what it measured."""
        result = standing_for(
            direction=Direction.BULLISH,
            close=Price.from_rupees("1420"),
            stop=Price.from_rupees("1400"),
            target=Price.from_rupees("1440"),
            expiry=EXPIRY,
            today=TODAY,
        )

        assert "1,420" in result.detail
        assert "1,400" in result.detail
        assert "1,440" in result.detail
