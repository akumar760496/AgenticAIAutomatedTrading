"""A chat the test drives: it answers with whatever the test queued."""

from __future__ import annotations

from datetime import UTC, datetime

from trading_desk.ports.chat import ChatUpdate, UpdateKind
from trading_desk.ports.notifier import DeliveryReceipt

NOW = datetime(2026, 10, 19, 4, 0, tzinfo=UTC)


class FakeChat:
    """Replays queued updates once, then returns nothing.

    Once, not forever: a capture loop reading the same tap repeatedly would
    record the same decision over and over and the test would still pass. The
    cursor behaviour is the thing most worth getting right here, so the fake
    honours it rather than ignoring `after_update_id`.
    """

    def __init__(self) -> None:
        self.updates: list[ChatUpdate] = []
        self.acknowledged: list[tuple[str, str]] = []
        self.prompts: list[str] = []
        self.polls = 0
        self.fail_next_acknowledge = False

    def queue_button(self, update_id: int, callback_data: str) -> None:
        self.updates.append(
            ChatUpdate(
                update_id=update_id,
                kind=UpdateKind.BUTTON,
                received_at=NOW,
                callback_data=callback_data,
                callback_id=f"cb-{update_id}",
            )
        )

    def queue_reply(self, update_id: int, text: str, quoted_text: str) -> None:
        self.updates.append(
            ChatUpdate(
                update_id=update_id,
                kind=UpdateKind.MESSAGE,
                received_at=NOW,
                text=text,
                quoted_text=quoted_text,
            )
        )

    def queue_message(self, update_id: int, text: str) -> None:
        self.updates.append(
            ChatUpdate(update_id=update_id, kind=UpdateKind.MESSAGE, received_at=NOW, text=text)
        )

    def poll(self, after_update_id: int, timeout_seconds: int) -> list[ChatUpdate]:
        del timeout_seconds  # the fake never waits
        self.polls += 1
        return [update for update in self.updates if update.update_id > after_update_id]

    def acknowledge_button(self, callback_id: str, note: str) -> None:
        if self.fail_next_acknowledge:
            # Separate from the other switches because this is the failure that
            # happens on its own schedule: Telegram refuses to answer a callback
            # older than about a minute, so any poll that was down produces it.
            self.fail_next_acknowledge = False
            raise RuntimeError(
                "answerCallbackQuery refused: HTTP 400 - Bad Request: query is "
                "too old and response timeout expired or query ID is invalid"
            )
        self.acknowledged.append((callback_id, note))

    def ask_for_reason(self, prompt: str) -> DeliveryReceipt:
        self.prompts.append(prompt)
        return DeliveryReceipt(message_id=f"prompt-{len(self.prompts)}", delivered_at=NOW)
