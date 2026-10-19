"""Verify step 3.6 - decision capture.

`docs/10`: done when "ten consecutive briefings have a recorded decision and
reason for every idea", and it calls this **the weakest link in v1**. `docs/12`
goes further: *"If this one fails, stop and redesign the capture mechanism
before continuing."*

So the headline check is a count of real briefings answered, and it reads 0
today. Everything above it checks the ways the mechanism could lose a decision
before it ever gets the chance: a tap with no reason, a reason with no tap, a
restart in between, and an update read twice.

The database check writes inside a transaction that is rolled back (ISS-024).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import Engine, text
from tests.fakes import FakeChat, FakeJournal, FakeNotifier, FrozenClock

from scripts.verify._runner import CheckResult
from trading_desk.application.decision_capture import NAG_AT_IST, DecisionCapture
from trading_desk.domain.capture import (
    MAX_CALLBACK_BYTES,
    DecisionAction,
    PendingReason,
    build_callback,
    reason_prompt,
)
from trading_desk.domain.enums import Direction
from trading_desk.domain.values import Money
from trading_desk.infra.config import load_infra_settings
from trading_desk.infra.db import create_db_engine
from trading_desk.ports.journal import RecordedIdea

TITLE = "Decision capture"

IDEA = UUID("11111111-2222-3333-4444-555555555555")
MORNING = datetime(2026, 10, 19, 3, 0, tzinfo=UTC)  # 08:30 IST
LATE = datetime(2026, 10, 19, 5, 0, tzinfo=UTC)  # 10:30 IST

REQUIRED_BRIEFINGS = 10

# An arbitrary cursor position, chosen to be well past the updates queued.
CURSOR_AFTER = 7


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
    now: datetime = MORNING, ideas: list[RecordedIdea] | None = None
) -> tuple[DecisionCapture, FakeChat, FakeJournal, FakeNotifier]:
    chat, journal, notifier = FakeChat(), FakeJournal(), FakeNotifier()
    for idea in ideas or [_idea()]:
        journal.record_idea(idea)
    return (
        DecisionCapture(chat=chat, journal=journal, notifier=notifier, clock=FrozenClock(now)),
        chat,
        journal,
        notifier,
    )


def _a_tap_and_a_reason_become_one_row() -> tuple[bool, str]:
    service, chat, journal, _ = _service()
    chat.queue_button(1, build_callback(DecisionAction.TOOK, IDEA))
    service.drain()
    chat.queue_reply(2, "the order win looked under-priced", quoted_text=chat.prompts[0])
    report = service.drain(after_update_id=1)

    recorded = journal.decisions
    ok = report.decisions_recorded == 1 and recorded[0].action == "TOOK"
    return ok, (
        f'1 tap + 1 reply -> 1 row: {recorded[0].action}, "{recorded[0].reason[:40]}"'
        if ok
        else f"{len(recorded)} rows recorded"
    )


def _a_tap_alone_records_nothing() -> tuple[bool, str]:
    """FR-7.4 requires the reason, so a tap is a question, not an answer."""
    service, chat, journal, _ = _service()
    chat.queue_button(1, build_callback(DecisionAction.SKIPPED, IDEA))
    report = service.drain()

    return not journal.decisions and report.prompts_sent == 1, (
        "a tap asks for a reason and writes nothing until it arrives"
    )


def _a_restart_between_the_two_loses_nothing() -> tuple[bool, str]:
    """Induced: a *second* service instance completes the first one's tap.

    This is the whole reason the idea id travels in the prompt text rather
    than in a dictionary - nothing is held between the tap and the reply.
    """
    first, chat, journal, notifier = _service()
    chat.queue_button(1, build_callback(DecisionAction.MODIFIED, IDEA))
    first.drain()

    second = DecisionCapture(chat=chat, journal=journal, notifier=notifier, clock=FrozenClock(LATE))
    chat.queue_reply(2, "halved it and took the near leg", quoted_text=chat.prompts[0])
    report = second.drain(after_update_id=1)

    ok = report.decisions_recorded == 1 and journal.decisions[0].action == "MODIFIED"
    return ok, (
        "a tap recorded by one process is completed by another, two hours later"
        if ok
        else "the decision was lost across the restart"
    )


def _a_handled_update_is_not_read_twice() -> tuple[bool, str]:
    service, chat, journal, _ = _service()
    prompt = reason_prompt(PendingReason(IDEA, DecisionAction.TOOK), "RELIANCE")
    chat.queue_reply(5, "took it", quoted_text=prompt)
    service.drain(after_update_id=5)

    return not journal.decisions, "an update at or below the cursor is not replayed"


def _the_cursor_advances_only_over_what_was_handled() -> tuple[bool, str]:
    service, chat, _, _ = _service()
    chat.queue_button(CURSOR_AFTER, build_callback(DecisionAction.TOOK, IDEA))
    report = service.drain(after_update_id=3)

    return (
        report.last_update_id == CURSOR_AFTER,
        f"cursor 3 -> {report.last_update_id} after 1 update",
    )


def _a_reason_with_no_prompt_is_answered() -> tuple[bool, str]:
    """A reason typed and silently dropped is worse than one never typed: the
    user believes it was recorded."""
    service, chat, journal, notifier = _service()
    chat.queue_message(1, "took it, the order was big")
    report = service.drain()

    answered = bool(notifier.alerts) and bool(report.unmatched) and not journal.decisions
    return answered, (
        "an unmatched reason gets a reply explaining how to record it"
        if answered
        else "an unmatched reason was swallowed"
    )


def _the_nag_fires_after_ten_and_not_before() -> tuple[bool, str]:
    """docs/11 §3."""
    early, _, _, early_notifier = _service(now=MORNING)
    late, _, _, late_notifier = _service(now=LATE)

    quiet = early.nag_if_due() is None and not early_notifier.alerts
    chased = late.nag_if_due() is not None and len(late_notifier.alerts) == 1
    return quiet and chased, (
        f"silent at 08:30 IST, one message at 10:30 (threshold {NAG_AT_IST:%H:%M})"
    )


def _three_unanswered_ideas_get_one_message() -> tuple[bool, str]:
    """Three notifications is how a nag becomes noise, and noise is how
    capture dies."""
    ideas = [_idea(idea_id=uuid4(), symbol=name) for name in ("RELIANCE", "INFY", "TCS")]
    service, _, _, notifier = _service(now=LATE, ideas=ideas)
    service.nag_if_due()

    return len(notifier.alerts) == 1, (
        f"{len(ideas)} unanswered ideas -> {len(notifier.alerts)} message"
    )


def _completeness_is_read_from_the_journal() -> tuple[bool, str]:
    """The weekly report. Counted from rows, not from a tally kept in memory -
    two counters drift, and the one that matters is the stored one."""
    second = uuid4()
    service, chat, journal, _ = _service(ideas=[_idea(), _idea(second, "INFY")])
    prompt = reason_prompt(PendingReason(IDEA, DecisionAction.TOOK), "RELIANCE")
    chat.queue_reply(1, "took it", quoted_text=prompt)
    service.drain()

    result = service.completeness_over([IDEA, second])
    del journal
    return result.answered == 1, result.as_line()


def _callback_data_fits_telegram() -> tuple[bool, str]:
    sizes = {action.value: len(build_callback(action, IDEA).encode()) for action in DecisionAction}
    largest = max(sizes.values())
    return largest <= MAX_CALLBACK_BYTES, (
        f"longest button payload {largest} bytes of {MAX_CALLBACK_BYTES} ({sizes})"
    )


def _the_journal_accepts_what_capture_writes() -> tuple[bool, str]:
    """The shape check, against the real tables, rolled back afterwards.

    A decision that passes every in-memory test and then fails on a column
    type is a decision lost at 09:00 on a Monday.
    """
    try:
        engine: Engine = create_db_engine(load_infra_settings())
        connection = engine.connect()
    except Exception as error:
        return False, f"database unreachable: {type(error).__name__}"

    transaction = connection.begin()
    try:
        idea_id = uuid4()
        connection.execute(
            text(
                "INSERT INTO briefing_ideas (idea_id, cycle_id, symbol, setup_id, direction, "
                "rank, confidence, gate_values, hypothetical_risk_paise, created_at, "
                "used_iv_proxy) VALUES "
                "(:idea_id, :cycle_id, 'DRILL', 'SETUP_A', 'BULLISH', 1, 0.6, "
                "CAST('{}' AS json), 1000, now(), false)"
            ),
            {"idea_id": idea_id, "cycle_id": uuid4()},
        )
        connection.execute(
            text(
                "INSERT INTO user_decisions (idea_id, action, reason, decided_at, "
                "recorded_at) VALUES (:idea_id, 'TOOK', 'drill', now(), now())"
            ),
            {"idea_id": idea_id},
        )
        count = connection.execute(
            text("SELECT count(*) FROM user_decisions WHERE idea_id = :idea_id"),
            {"idea_id": idea_id},
        ).scalar_one()
    except Exception as error:
        return False, f"the journal refused the row: {str(error).splitlines()[0][:70]}"
    finally:
        transaction.rollback()
        connection.close()

    return int(count) == 1, "a TOOK decision with a reason is accepted, then rolled back"


def _ten_briefings_have_been_answered() -> tuple[bool, str]:
    """The actual done-when, read from the real journal.

    `docs/12`: if this fails, stop and redesign the capture mechanism before
    continuing. It reads 0 today because no briefing has been sent yet - which
    is a different thing from a mechanism that does not work, and the number
    is the only honest way to say so.
    """
    try:
        engine = create_db_engine(load_infra_settings())
        with engine.connect() as connection:
            answered = connection.execute(
                text(
                    "SELECT count(DISTINCT idea.cycle_id) FROM briefing_ideas idea "
                    "WHERE NOT EXISTS (SELECT 1 FROM briefing_ideas other "
                    "WHERE other.cycle_id = idea.cycle_id AND NOT EXISTS "
                    "(SELECT 1 FROM user_decisions d WHERE d.idea_id = other.idea_id))"
                )
            ).scalar_one()
            briefed = connection.execute(
                text("SELECT count(DISTINCT cycle_id) FROM briefing_ideas")
            ).scalar_one()
    except Exception as error:
        return False, f"database unreachable: {type(error).__name__}"

    return int(answered) >= REQUIRED_BRIEFINGS, (
        f"{int(answered)} of {REQUIRED_BRIEFINGS} fully-answered briefings "
        f"({int(briefed)} briefings recorded so far)"
    )


def run() -> list[CheckResult]:
    checks = [
        (
            "a_tap_and_a_reason_become_one_row",
            _a_tap_and_a_reason_become_one_row,
            "need the decision and the reason stored together (FR-7.4)",
        ),
        (
            "a_tap_alone_records_nothing",
            _a_tap_alone_records_nothing,
            "need no row until the reason arrives",
        ),
        (
            "a_restart_between_the_two_loses_nothing",
            _a_restart_between_the_two_loses_nothing,
            "need a second process to complete the first one's tap",
        ),
        (
            "a_handled_update_is_not_read_twice",
            _a_handled_update_is_not_read_twice,
            "need the cursor to suppress a replay",
        ),
        (
            "the_cursor_advances_only_over_what_was_handled",
            _the_cursor_advances_only_over_what_was_handled,
            "need the new cursor returned to the caller",
        ),
        (
            "a_reason_with_no_prompt_is_answered",
            _a_reason_with_no_prompt_is_answered,
            "need a reply, never a silent drop",
        ),
        (
            "the_nag_fires_after_ten_and_not_before",
            _the_nag_fires_after_ten_and_not_before,
            "need silence before 10:00 IST and one message after (docs/11)",
        ),
        (
            "three_unanswered_ideas_get_one_message",
            _three_unanswered_ideas_get_one_message,
            "need 1 message - a nag per idea is noise",
        ),
        (
            "completeness_is_read_from_the_journal",
            _completeness_is_read_from_the_journal,
            "need the count from stored rows, not a tally",
        ),
        (
            "callback_data_fits_telegram",
            _callback_data_fits_telegram,
            f"need <= {MAX_CALLBACK_BYTES} bytes per button",
        ),
        (
            "the_journal_accepts_what_capture_writes",
            _the_journal_accepts_what_capture_writes,
            "need the real tables to take the real row, then roll back",
        ),
        (
            "ten_briefings_have_been_answered",
            _ten_briefings_have_been_answered,
            f"need {REQUIRED_BRIEFINGS} - the done-when, and docs/12's stop condition",
        ),
    ]
    results = []
    for name, check, requirement in checks:
        passed, measured = check()
        results.append(
            CheckResult(name=name, passed=passed, measured=measured, requirement=requirement)
        )
    return results
