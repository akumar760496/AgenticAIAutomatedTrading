"""What we need to reach the user.

v1's entire output path. A briefing that is not delivered did not happen, so
delivery returns a confirmation rather than fire-and-forget.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID


class AlertLevel(StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"  # feed down, cycle failed, cap breached - wake the user


@dataclass(frozen=True)
class DeliveryReceipt:
    message_id: str
    delivered_at: datetime


class NotifierPort(Protocol):
    def send_briefing(self, markdown: str) -> DeliveryReceipt:
        """Deliver the daily briefing. Raises if it could not be delivered.

        One call may become several messages: a briefing with every gate value
        in it is longer than any chat app will accept in one. Splitting is the
        adapter's job, because the limit is a fact about the destination, and
        the receipt returned is for the first message - the one that carries
        the decision.
        """
        ...

    def ask_decision(self, idea_id: UUID, markdown: str) -> DeliveryReceipt:
        """Deliver one idea with a way to answer it.

        Deliberately not called "send_buttons": asking someone to decide is the
        thing we need, and an inline keyboard is only how Telegram spells it.
        An email adapter would spell it with three links.
        """
        ...

    def send_alert(self, level: AlertLevel, message: str) -> DeliveryReceipt:
        """Deliver an operational alert."""
        ...
