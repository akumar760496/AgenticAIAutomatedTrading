"""Listening to Telegram: tapped buttons and typed replies.

`getUpdates` long polling rather than a webhook, for the reason in
`ports/chat.py`: a webhook needs a public HTTPS endpoint and this runs on a
machine that should not have one.

**The offset is how an update is acknowledged.** Asking for updates after id N
tells Telegram that everything up to N has been handled, and it will never
send them again. So the cursor must only move once the work is done - which is
why `poll` does not advance it and `DecisionCapture` returns the new value for
the caller to persist.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from trading_desk.adapters.telegram.client import TelegramClient
from trading_desk.ports.chat import ChatUpdate, UpdateKind
from trading_desk.ports.notifier import DeliveryReceipt


class TelegramChat:
    """Implements ChatPort against the Bot API."""

    def __init__(self, client: TelegramClient) -> None:
        self._client = client

    def poll(self, after_update_id: int, timeout_seconds: int) -> list[ChatUpdate]:
        raw = self._client.call_list(
            "getUpdates",
            {"offset": after_update_id + 1, "timeout": timeout_seconds},
        )
        return [_to_update(item) for item in raw]

    def acknowledge_button(self, callback_id: str, note: str) -> None:
        self._client.call("answerCallbackQuery", {"callback_query_id": callback_id, "text": note})

    def ask_for_reason(self, prompt: str) -> DeliveryReceipt:
        result = self._client.call(
            "sendMessage",
            {
                "chat_id": self._client.chat_id,
                "text": prompt,
                # force_reply is what makes the answer quote this message, and
                # the quote is what carries the idea id back.
                "reply_markup": {"force_reply": True},
            },
        )
        return DeliveryReceipt(
            message_id=str(result.get("message_id", "")),
            delivered_at=datetime.now(UTC),
        )


def _to_update(raw: dict[str, Any]) -> ChatUpdate:
    update_id = int(raw.get("update_id", 0))

    callback = raw.get("callback_query")
    if isinstance(callback, dict):
        return ChatUpdate(
            update_id=update_id,
            kind=UpdateKind.BUTTON,
            received_at=_sent_at(callback.get("message")),
            callback_data=str(callback.get("data", "")),
            callback_id=str(callback.get("id", "")),
        )

    message = raw.get("message")
    if isinstance(message, dict):
        quoted = message.get("reply_to_message")
        return ChatUpdate(
            update_id=update_id,
            kind=UpdateKind.MESSAGE,
            received_at=_sent_at(message),
            text=str(message.get("text", "")),
            quoted_text=str(quoted.get("text", "")) if isinstance(quoted, dict) else "",
        )

    # Anything else still comes back, so the caller can advance the cursor past
    # it. An update nobody handles must not be an update nobody acknowledges.
    return ChatUpdate(update_id=update_id, kind=UpdateKind.OTHER, received_at=datetime.now(UTC))


def _sent_at(message: Any) -> datetime:
    """Telegram sends a Unix timestamp; everything internal is tz-aware UTC."""
    if isinstance(message, dict) and "date" in message:
        return datetime.fromtimestamp(int(message["date"]), tz=UTC)
    return datetime.now(UTC)
