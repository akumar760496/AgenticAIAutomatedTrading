"""Writes notifications to the log instead of sending them.

Telegram arrives in step 3.5. Until then the snapshotter still needs somewhere to
report, and a log line that a human may not read is better than a report that does
not exist - it is at least in the file when something is being diagnosed.

CRITICAL is logged at ERROR so it stands out in a file nobody is watching live.
"""

from __future__ import annotations

import logging
from uuid import UUID

from trading_desk.ports.clock import Clock
from trading_desk.ports.notifier import AlertLevel, DeliveryReceipt

logger = logging.getLogger("trading_desk.notify")

_LEVEL_MAP = {
    AlertLevel.INFO: logging.INFO,
    AlertLevel.WARNING: logging.WARNING,
    AlertLevel.CRITICAL: logging.ERROR,
}


class LoggingNotifier:
    """Implements NotifierPort by logging. A stand-in, not a destination."""

    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self._sent = 0

    def _receipt(self) -> DeliveryReceipt:
        self._sent += 1
        return DeliveryReceipt(message_id=f"log-{self._sent}", delivered_at=self._clock.utc_now())

    def send_briefing(self, markdown: str) -> DeliveryReceipt:
        logger.info("BRIEFING\n%s", markdown)
        return self._receipt()

    def send_alert(self, level: AlertLevel, message: str) -> DeliveryReceipt:
        logger.log(_LEVEL_MAP[level], "[%s] %s", level, message)
        return self._receipt()

    def ask_decision(self, idea_id: UUID, markdown: str) -> DeliveryReceipt:
        """Logs the question nobody can answer here.

        A log file has no buttons, so this records what would have been asked.
        The capture loop in step 3.6 needs a real chat; this stand-in exists so
        the rest of the system runs without one.
        """
        logger.info("DECISION NEEDED [%s]\n%s", idea_id, markdown)
        return self._receipt()
