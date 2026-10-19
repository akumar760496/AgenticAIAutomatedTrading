"""A notifier that records instead of sending."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from trading_desk.ports.notifier import AlertLevel, DeliveryReceipt


class FakeNotifier:
    """Collects messages so a test can assert on what the user would have seen.

    fail_next_send exists because delivery failure is a real path: a briefing that
    was not delivered did not happen, and the system has to notice.

    fail_next_alert is separate on purpose. An unreachable notifier is exactly
    the situation in which something else is also failing, so "the alert about
    the failure could not be sent either" is its own path and needs its own
    switch (`application/scheduler.py`).
    """

    def __init__(self) -> None:
        self.briefings: list[str] = []
        self.alerts: list[tuple[AlertLevel, str]] = []
        self.decisions_asked: list[tuple[UUID, str]] = []
        self.fail_next_send = False
        self.fail_next_alert = False

    def _receipt(self) -> DeliveryReceipt:
        return DeliveryReceipt(
            message_id=f"fake-{len(self.briefings) + len(self.alerts)}",
            delivered_at=datetime(2026, 10, 16, 2, 45, tzinfo=UTC),
        )

    def send_briefing(self, markdown: str) -> DeliveryReceipt:
        if self.fail_next_send:
            self.fail_next_send = False
            raise ConnectionError("Simulated delivery failure")
        self.briefings.append(markdown)
        return self._receipt()

    def send_alert(self, level: AlertLevel, message: str) -> DeliveryReceipt:
        if self.fail_next_alert:
            self.fail_next_alert = False
            raise ConnectionError("Simulated alert failure")
        self.alerts.append((level, message))
        return self._receipt()

    def ask_decision(self, idea_id: UUID, markdown: str) -> DeliveryReceipt:
        if self.fail_next_send:
            self.fail_next_send = False
            raise ConnectionError("Simulated delivery failure")
        self.decisions_asked.append((idea_id, markdown))
        return self._receipt()
