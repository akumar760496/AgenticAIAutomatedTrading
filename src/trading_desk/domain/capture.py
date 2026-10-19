"""Recording what you decided, and why.

`docs/10` calls decision capture "the weakest link in v1" and `docs/12` says
that if capture completeness fails, stop and redesign before continuing. So
the mechanism is built to survive the two things that actually break it: a
restart, and a reason typed four hours later.

**The message trail is the state.** A tap on "Took it" is followed by a prompt
that asks for the reason *and carries the idea id in its own text*; the reply
quotes that prompt, so the id comes back with the answer. Nothing is held in
memory between the tap and the reply. A process restart loses nothing, and a
decision tapped at 09:00 can still be explained at 14:00.

The alternative - a dictionary of "who owes me a reason" - works until the
first restart and then drops a decision silently, which is the worst available
failure for the one dataset this project is built to collect.

**A decision without a reason is not recorded.** `RecordedDecision` raises on
an empty reason, and that is deliberate: the override reasons are described in
`docs/06` §4 as the most valuable field in the schema. A blank one is worse
than a missing row, because a missing row is visible in the completeness count.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

# Telegram allows 64 bytes of callback data. "d:MODIFIED:" plus a 36-character
# UUID is 47, so the longest action still fits with room to spare.
CALLBACK_PREFIX = "d"
MAX_CALLBACK_BYTES = 64

_UUID_PATTERN = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


class DecisionAction(StrEnum):
    """The three answers. `docs/06` §4 names exactly these."""

    TOOK = "TOOK"
    SKIPPED = "SKIPPED"
    MODIFIED = "MODIFIED"


class CallbackError(ValueError):
    """Button data that did not come from us, or came from an older version."""


@dataclass(frozen=True)
class PendingReason:
    """A tap waiting for its reason. Reconstructed from the quoted message."""

    idea_id: UUID
    action: DecisionAction


def build_callback(action: DecisionAction, idea_id: UUID) -> str:
    """The data a button carries back when it is tapped."""
    payload = f"{CALLBACK_PREFIX}:{action.value}:{idea_id}"
    if len(payload.encode()) > MAX_CALLBACK_BYTES:
        raise CallbackError(f"callback data is {len(payload.encode())} bytes, max is 64")
    return payload


def parse_callback(payload: str) -> PendingReason:
    """Read a tapped button, or raise naming what arrived.

    Raises rather than returning None: a button press we cannot read is a
    decision we are about to lose, and it should be loud.
    """
    parts = payload.split(":")
    expected_parts = 3
    if len(parts) != expected_parts or parts[0] != CALLBACK_PREFIX:
        raise CallbackError(f"not one of our buttons: {payload!r}")
    try:
        action = DecisionAction(parts[1])
    except ValueError:
        raise CallbackError(f"unknown action {parts[1]!r}") from None
    try:
        idea_id = UUID(parts[2])
    except ValueError:
        raise CallbackError(f"not an idea id: {parts[2]!r}") from None
    return PendingReason(idea_id=idea_id, action=action)


def reason_prompt(pending: PendingReason, symbol: str) -> str:
    """The message that asks for a reason and carries the id to get it back.

    The id is visible rather than hidden in metadata. It is slightly ugly in
    the chat and it means a human reading the history a year later can see
    which idea a reason belonged to without a database.
    """
    return (
        f"{pending.action.value}: {symbol}\n"
        f"Reply to this message with your reason - one line is enough.\n"
        f"[{pending.action.value} {pending.idea_id}]"
    )


def parse_reason_reply(quoted_text: str) -> PendingReason:
    """Recover the idea and the action from the message being replied to."""
    found = _UUID_PATTERN.search(quoted_text)
    if found is None:
        raise CallbackError("the quoted message carries no idea id")
    action = next(
        (item for item in DecisionAction if item.value in quoted_text),
        None,
    )
    if action is None:
        raise CallbackError("the quoted message carries no action")
    return PendingReason(idea_id=UUID(found.group()), action=action)


@dataclass(frozen=True)
class CaptureCompleteness:
    """How much of the briefing you answered. FR-7.10, and Gate 2's input."""

    ideas: int
    answered: int

    @property
    def unanswered(self) -> int:
        return self.ideas - self.answered

    @property
    def fraction(self) -> float:
        """1.0 when there was nothing to answer.

        A week with no ideas is not a week of poor capture, and scoring it as
        0% would make the metric punish the system for being correctly quiet.
        """
        if self.ideas == 0:
            return 1.0
        return self.answered / self.ideas

    def as_line(self) -> str:
        if self.ideas == 0:
            return "no ideas to answer this period"
        return f"you answered {self.answered} of {self.ideas} ({self.fraction:.0%})"


def completeness(idea_ids: list[UUID], answered_ids: set[UUID]) -> CaptureCompleteness:
    answered = sum(1 for idea_id in idea_ids if idea_id in answered_ids)
    return CaptureCompleteness(ideas=len(idea_ids), answered=answered)
