"""Recording a decision, and chasing one that never arrives.

`docs/10` calls this the weakest link in v1, so the tests are biased towards
the ways it loses data: a tap with no reason, a reason with no tap, a restart
in between, and the same update being read twice.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from tests.fakes import FakeChat, FakeJournal, FakeNotifier, FrozenClock
from trading_desk.application.decision_capture import DecisionCapture
from trading_desk.domain.capture import DecisionAction, PendingReason, build_callback, reason_prompt
from trading_desk.domain.enums import Direction
from trading_desk.domain.values import Money
from trading_desk.ports.journal import RecordedIdea

IDEA = UUID("11111111-2222-3333-4444-555555555555")
MORNING = datetime(2026, 10, 19, 3, 0, tzinfo=UTC)  # 08:30 IST
LATE = datetime(2026, 10, 19, 5, 0, tzinfo=UTC)  # 10:30 IST


def _idea(idea_id: UUID = IDEA, symbol: str = "RELIANCE") -> RecordedIdea:
    return RecordedIdea(
        idea_id=idea_id,
        cycle_id=uuid4(),
        symbol=symbol,
        setup_id="SETUP_A",
        direction=Direction.BULLISH,
        rank=1,
        confidence=0.6,
        gate_values={},
        hypothetical_risk=Money.from_rupees(Decimal("5000")),
        created_at=MORNING,
    )


def _service(
    now: datetime = MORNING,
) -> tuple[DecisionCapture, FakeChat, FakeJournal, FakeNotifier]:
    chat, journal, notifier = FakeChat(), FakeJournal(), FakeNotifier()
    journal.record_idea(_idea())
    service = DecisionCapture(chat=chat, journal=journal, notifier=notifier, clock=FrozenClock(now))
    return service, chat, journal, notifier


class TestTheHappyPath:
    def test_a_tap_asks_for_a_reason_and_records_nothing_yet(self) -> None:
        """FR-7.4 requires a reason, so a tap alone is not a decision."""
        service, chat, journal, _ = _service()
        chat.queue_button(1, build_callback(DecisionAction.TOOK, IDEA))

        report = service.drain()

        assert report.prompts_sent == 1
        assert journal.decisions == []

    def test_the_prompt_names_the_instrument(self) -> None:
        service, chat, _, _ = _service()
        chat.queue_button(1, build_callback(DecisionAction.TOOK, IDEA))

        service.drain()

        assert "RELIANCE" in chat.prompts[0]

    def test_the_reply_completes_the_decision(self) -> None:
        service, chat, journal, _ = _service()
        prompt = reason_prompt(PendingReason(IDEA, DecisionAction.TOOK), "RELIANCE")
        chat.queue_reply(2, "liked the order win", quoted_text=prompt)

        report = service.drain()

        assert report.decisions_recorded == 1
        decision = journal.decisions[0]
        assert decision.idea_id == IDEA
        assert decision.action == "TOOK"
        assert decision.reason == "liked the order win"

    def test_the_button_is_acknowledged_so_it_stops_spinning(self) -> None:
        service, chat, _, _ = _service()
        chat.queue_button(1, build_callback(DecisionAction.SKIPPED, IDEA))

        service.drain()

        assert chat.acknowledged[0][1].startswith("SKIPPED")


class TestWhatItRefusesToLose:
    def test_a_reason_with_no_quoted_prompt_is_answered_not_swallowed(self) -> None:
        """A reason typed and silently dropped is worse than one never typed:
        the user believes it was recorded."""
        service, chat, journal, notifier = _service()
        chat.queue_message(1, "I took it because the order looked big")

        report = service.drain()

        assert journal.decisions == []
        assert report.unmatched
        assert notifier.alerts

    def test_a_restart_between_the_tap_and_the_reason_loses_nothing(self) -> None:
        """Nothing is held in memory between the two, so a second service
        instance can complete a decision the first one asked for."""
        first, chat, journal, notifier = _service()
        chat.queue_button(1, build_callback(DecisionAction.MODIFIED, IDEA))
        first.drain()
        prompt = chat.prompts[0]

        second = DecisionCapture(
            chat=chat, journal=journal, notifier=notifier, clock=FrozenClock(LATE)
        )
        chat.queue_reply(2, "halved it", quoted_text=prompt)
        report = second.drain(after_update_id=1)

        assert report.decisions_recorded == 1
        assert journal.decisions[0].action == "MODIFIED"

    def test_the_cursor_only_advances_past_what_was_handled(self) -> None:
        service, chat, _, _ = _service()
        chat.queue_button(7, build_callback(DecisionAction.TOOK, IDEA))

        report = service.drain(after_update_id=3)

        assert report.last_update_id == 7

    def test_an_already_handled_update_is_not_read_again(self) -> None:
        service, chat, journal, _ = _service()
        prompt = reason_prompt(PendingReason(IDEA, DecisionAction.TOOK), "RELIANCE")
        chat.queue_reply(5, "took it", quoted_text=prompt)

        service.drain(after_update_id=5)

        assert journal.decisions == []

    def test_an_unreadable_button_is_reported_rather_than_ignored(self) -> None:
        service, chat, _, _ = _service()
        chat.queue_button(1, "someone_elses_button")

        report = service.drain()

        assert report.unmatched
        assert report.prompts_sent == 0


class TestTheNag:
    def test_nothing_is_sent_before_ten(self) -> None:
        service, _, _, notifier = _service(now=MORNING)

        assert service.nag_if_due() is None
        assert notifier.alerts == []

    def test_after_ten_an_unanswered_idea_is_chased_once(self) -> None:
        service, _, _, notifier = _service(now=LATE)

        text = service.nag_if_due()

        assert text is not None
        assert "RELIANCE" in text
        assert len(notifier.alerts) == 1

    def test_one_message_for_three_ideas_not_three_messages(self) -> None:
        """Three notifications is how a nag becomes noise, and noise is how
        capture dies."""
        chat, journal, notifier = FakeChat(), FakeJournal(), FakeNotifier()
        for symbol in ("RELIANCE", "INFY", "TCS"):
            journal.record_idea(_idea(idea_id=uuid4(), symbol=symbol))
        service = DecisionCapture(
            chat=chat, journal=journal, notifier=notifier, clock=FrozenClock(LATE)
        )

        service.nag_if_due()

        assert len(notifier.alerts) == 1
        assert notifier.alerts[0][1].count("#1") == 3

    def test_an_answered_briefing_is_not_chased(self) -> None:
        service, chat, _journal, _notifier = _service(now=LATE)
        prompt = reason_prompt(PendingReason(IDEA, DecisionAction.TOOK), "RELIANCE")
        chat.queue_reply(1, "took it", quoted_text=prompt)
        service.drain()

        assert service.nag_if_due() is None


def test_completeness_reads_the_journal_not_a_counter() -> None:
    service, chat, journal, _ = _service()
    second = uuid4()
    journal.record_idea(_idea(idea_id=second, symbol="INFY"))
    prompt = reason_prompt(PendingReason(IDEA, DecisionAction.TOOK), "RELIANCE")
    chat.queue_reply(1, "took it", quoted_text=prompt)
    service.drain()

    result = service.completeness_over([IDEA, second])

    assert result.as_line() == "you answered 1 of 2 (50%)"
