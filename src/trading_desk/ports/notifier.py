"""What we need to reach the user.

v1's entire output path. A briefing that is not delivered did not happen, so
delivery returns a confirmation rather than fire-and-forget.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol


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
        """Deliver the daily briefing. Raises if it could not be delivered."""
        ...

    def send_alert(self, level: AlertLevel, message: str) -> DeliveryReceipt:
        """Deliver an operational alert."""
        ...
