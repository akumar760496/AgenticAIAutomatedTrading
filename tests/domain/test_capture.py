"""Encoding a decision, and getting it back hours later.

The test that matters is `test_a_reason_typed_hours_later_still_finds_its_idea`.
Everything else here is parsing; that one is the design decision - the message
trail is the state, so a restart between the tap and the reason loses nothing.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from trading_desk.domain.capture import (
    MAX_CALLBACK_BYTES,
    CallbackError,
    DecisionAction,
    PendingReason,
    build_callback,
    completeness,
    parse_callback,
    parse_reason_reply,
    reason_prompt,
)

IDEA = UUID("11111111-2222-3333-4444-555555555555")


class TestButtons:
    def test_a_tap_round_trips(self) -> None:
        payload = build_callback(DecisionAction.TOOK, IDEA)

        assert parse_callback(payload) == PendingReason(idea_id=IDEA, action=DecisionAction.TOOK)

    @pytest.mark.parametrize("action", list(DecisionAction))
    def test_every_action_fits_telegram_s_limit(self, action: DecisionAction) -> None:
        """64 bytes. MODIFIED plus a UUID is the longest it can get."""
        assert len(build_callback(action, IDEA).encode()) <= MAX_CALLBACK_BYTES

    def test_someone_else_s_button_raises(self) -> None:
        with pytest.raises(CallbackError, match="not one of our buttons"):
            parse_callback("do_something_else")

    def test_an_unknown_action_raises_rather_than_guessing(self) -> None:
        with pytest.raises(CallbackError, match="unknown action"):
            parse_callback(f"d:SOLD:{IDEA}")

    def test_a_broken_id_raises(self) -> None:
        with pytest.raises(CallbackError, match="not an idea id"):
            parse_callback("d:TOOK:not-a-uuid")


class TestTheReasonReply:
    def test_the_prompt_carries_the_idea_and_the_action(self) -> None:
        prompt = reason_prompt(PendingReason(IDEA, DecisionAction.SKIPPED), "RELIANCE")

        assert str(IDEA) in prompt
        assert "SKIPPED" in prompt
        assert "RELIANCE" in prompt

    def test_a_reason_typed_hours_later_still_finds_its_idea(self) -> None:
        """No session state exists, so there is nothing for a restart to lose."""
        prompt = reason_prompt(PendingReason(IDEA, DecisionAction.TOOK), "RELIANCE")

        recovered = parse_reason_reply(prompt)

        assert recovered == PendingReason(idea_id=IDEA, action=DecisionAction.TOOK)

    def test_a_quoted_message_with_no_id_raises(self) -> None:
        with pytest.raises(CallbackError, match="no idea id"):
            parse_reason_reply("Good morning")

    def test_a_quoted_message_with_no_action_raises(self) -> None:
        with pytest.raises(CallbackError, match="no action"):
            parse_reason_reply(f"something about {IDEA}")


class TestCompleteness:
    def test_four_of_five(self) -> None:
        ideas = [uuid4() for _ in range(5)]
        result = completeness(ideas, set(ideas[:4]))

        assert result.answered == 4
        assert result.unanswered == 1
        assert "4 of 5" in result.as_line()

    def test_a_quiet_week_is_not_a_failed_week(self) -> None:
        """No ideas means nothing to answer. Scoring it 0% would punish the
        system for being correctly quiet, which is 40-60% of sessions."""
        result = completeness([], set())

        assert result.fraction == 1.0
        assert "no ideas" in result.as_line()

    def test_a_decision_on_an_idea_from_another_week_does_not_count(self) -> None:
        ideas = [uuid4()]
        result = completeness(ideas, {uuid4()})

        assert result.answered == 0
