"""Listening for your answer, and chasing it when it does not come.

`docs/10` calls this the weakest link in v1 and it is right: everything
downstream - calibration, the stop criteria, whether any of this worked - is
computed from rows that only exist if you answer. So the design is biased
towards recording *something* rather than towards elegance.

**A tap is not a decision.** `RecordedDecision` refuses an empty reason
(FR-7.4 says the reason is required), so a tap only becomes a row once the
reason arrives. Between the two there is nothing held in memory: the prompt
carries the idea id in its own text and the reply quotes it back
(`domain/capture.py`). A restart in between loses nothing.

**An unparseable message is answered, not swallowed.** If someone types a
reason without replying to the prompt, the loop says so rather than dropping
it, because a reason typed and lost is worse than one never typed - the user
believes it was recorded.

**The nag is the backstop** (`docs/11` §3): at 10:00, anything from this
morning with no decision gets one message listing it. One message, not one per
idea, because three notifications is how a nag becomes noise and noise is how
capture dies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time
from uuid import UUID

from trading_desk.domain.capture import (
    CallbackError,
    CaptureCompleteness,
    DecisionAction,
    completeness,
    parse_callback,
    parse_reason_reply,
    reason_prompt,
)
from trading_desk.domain.timezones import to_ist
from trading_desk.ports.chat import ChatPort, ChatUpdate, UpdateKind
from trading_desk.ports.clock import Clock
from trading_desk.ports.journal import JournalPort, RecordedDecision
from trading_desk.ports.notifier import AlertLevel, NotifierPort

# docs/11 §3: "a nag at 10:00 for unanswered ideas".
NAG_AT_IST = time(10, 0)

# How long one poll waits on the server before returning empty. Long enough
# that an idle loop is nearly free, short enough that a shutdown is not stuck.
POLL_SECONDS = 25

HELP_TEXT = (
    "To record a decision, tap a button on an idea and then *reply* to the "
    "question it asks. A reason typed on its own cannot be matched to an idea."
)


@dataclass
class CaptureReport:
    """What one pass through the inbox did."""

    updates_seen: int = 0
    decisions_recorded: int = 0
    prompts_sent: int = 0
    unmatched: list[str] = field(default_factory=list)
    last_update_id: int = 0

    def as_lines(self) -> list[str]:
        lines = [
            f"{self.updates_seen} update(s): {self.prompts_sent} asked, "
            f"{self.decisions_recorded} recorded"
        ]
        lines.extend(f"  unmatched: {item}" for item in self.unmatched)
        return lines


class DecisionCapture:
    def __init__(
        self,
        *,
        chat: ChatPort,
        journal: JournalPort,
        notifier: NotifierPort,
        clock: Clock,
    ) -> None:
        self._chat = chat
        self._journal = journal
        self._notifier = notifier
        self._clock = clock

    def drain(self, after_update_id: int = 0) -> CaptureReport:
        """Handle everything waiting, and report the new cursor.

        The cursor is returned rather than stored: it is one integer, and the
        caller is the only thing that knows whether the work it triggered
        succeeded.
        """
        report = CaptureReport(last_update_id=after_update_id)
        for update in self._chat.poll(after_update_id, POLL_SECONDS):
            report.updates_seen += 1
            report.last_update_id = max(report.last_update_id, update.update_id)
            self._handle(update, report)
        return report

    def _handle(self, update: ChatUpdate, report: CaptureReport) -> None:
        if update.kind is UpdateKind.BUTTON:
            self._handle_button(update, report)
            return
        if update.kind is UpdateKind.MESSAGE:
            self._handle_message(update, report)

    def _handle_button(self, update: ChatUpdate, report: CaptureReport) -> None:
        try:
            pending = parse_callback(update.callback_data)
        except CallbackError as error:
            report.unmatched.append(str(error))
            return

        symbol = self._symbol_for(pending.idea_id)
        self._chat.acknowledge_button(update.callback_id, f"{pending.action.value} - why?")
        self._chat.ask_for_reason(reason_prompt(pending, symbol))
        report.prompts_sent += 1

    def _handle_message(self, update: ChatUpdate, report: CaptureReport) -> None:
        if not update.text.strip():
            return
        if not update.quoted_text:
            self._notifier.send_alert(AlertLevel.INFO, HELP_TEXT)
            report.unmatched.append(f"a reason with no quoted prompt: {update.text[:40]!r}")
            return

        try:
            pending = parse_reason_reply(update.quoted_text)
        except CallbackError as error:
            self._notifier.send_alert(AlertLevel.INFO, HELP_TEXT)
            report.unmatched.append(str(error))
            return

        self._journal.record_decision(
            RecordedDecision(
                idea_id=pending.idea_id,
                action=pending.action.value,
                reason=update.text.strip(),
                decided_at=update.received_at,
            )
        )
        report.decisions_recorded += 1

    def _symbol_for(self, idea_id: UUID) -> str:
        """The symbol, so the prompt reads like a question about something.

        Falls back to the id rather than raising: a prompt that cannot name
        the instrument is still a prompt that captures a reason.
        """
        for idea in self._journal.ideas_awaiting_decision(self._clock.utc_now()):
            if idea.idea_id == idea_id:
                return idea.symbol
        return str(idea_id)[:8]

    # ----------------------------------------------------------------- nag

    def nag_if_due(self, *, now: datetime | None = None) -> str | None:
        """One message listing everything still unanswered, after 10:00 IST.

        Returns the text sent, or None. Returning it rather than only sending
        makes the "what would it have said?" question answerable in a test and
        in a dry run.
        """
        moment = now or self._clock.utc_now()
        if to_ist(moment).time() < NAG_AT_IST:
            return None

        waiting = self._journal.ideas_awaiting_decision(moment)
        if not waiting:
            return None

        lines = [f"{len(waiting)} idea(s) from today still have no decision:"]
        lines.extend(
            f"  {idea.symbol} #{idea.rank} ({', '.join(a.value for a in DecisionAction)})"
            for idea in waiting
        )
        text = "\n".join(lines)
        self._notifier.send_alert(AlertLevel.WARNING, text)
        return text

    def completeness_over(self, idea_ids: list[UUID]) -> CaptureCompleteness:
        """ "You answered 4 of 5" - docs/10's weekly report, as a number."""
        answered = {
            idea_id for idea_id in idea_ids if self._journal.latest_decision(idea_id) is not None
        }
        return completeness(idea_ids, answered)
