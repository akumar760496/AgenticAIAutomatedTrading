"""The inbound half of the conversation.

`NotifierPort` is one-way: it delivers a briefing and reports that it arrived.
Step 3.6 has to *listen* - for a tapped button and for the reason typed after
it - and listening is a different capability with a different failure mode, so
it is a different port.

**Long polling, not a webhook.** A webhook needs a public HTTPS endpoint with a
certificate and an open port. This runs on a laptop that reaches out to the
internet perfectly well and should not be reachable from it. `poll` blocks for
up to `timeout_seconds` on the server side, which costs nothing and arrives
within a second of a tap.

**Updates are consumed by id, not by time.** Telegram keeps an update until it
is acknowledged by asking for a later one, so the cursor is the only thing that
must survive a restart - and it is a single integer the caller owns.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from trading_desk.ports.notifier import DeliveryReceipt


class UpdateKind(StrEnum):
    BUTTON = "BUTTON"  # an inline keyboard was tapped
    MESSAGE = "MESSAGE"  # the user typed something
    OTHER = "OTHER"  # anything we do not handle, kept so the cursor advances


@dataclass(frozen=True)
class ChatUpdate:
    """One thing the user did.

    `quoted_text` is the message being replied to, and it is how a typed reason
    finds its way back to the idea it is about (`domain/capture.py`). Empty
    when the message was not a reply.
    """

    update_id: int
    kind: UpdateKind
    received_at: datetime
    text: str = ""
    quoted_text: str = ""
    callback_data: str = ""
    callback_id: str = ""


class ChatPort(Protocol):
    def poll(self, after_update_id: int, timeout_seconds: int) -> list[ChatUpdate]:
        """Updates newer than `after_update_id`, oldest first.

        Blocks up to `timeout_seconds` waiting for one. Returning an empty list
        is the normal case and is not an error.
        """
        ...

    def acknowledge_button(self, callback_id: str, note: str) -> None:
        """Stop the tapped button spinning, and show a short confirmation.

        Telegram requires this within seconds of a tap. Without it the button
        looks stuck, which reads as "the system is broken" at exactly the
        moment the user is deciding whether to trust it.
        """
        ...

    def ask_for_reason(self, prompt: str) -> DeliveryReceipt:
        """Send a message that asks to be replied to.

        The reply carries the prompt's text back, which is what lets a reason
        typed hours later still find its idea.
        """
        ...
