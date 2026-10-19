"""Delivering a briefing to Telegram.

Two things this does that the port deliberately does not describe, because
both are facts about Telegram rather than about delivering a briefing:

**Splitting at 4096 characters.** A briefing with every gate value in it is
longer than one message. `split_message` breaks on blank lines first, then on
newlines, and only cuts mid-line when a single line is itself too long - so a
split lands between sections rather than between a number and its units.

**Plain text, no parse_mode.** MarkdownV2 would require escaping `. - ( ) ! +`
and this text is made of them: "₹10,500.00", "0.52 x ATR", "(proxy)". One
missed escape is an HTTP 400, which is a briefing that did not arrive - the
exact failure this step exists to prevent. The formatting that survives is
indentation and blank lines, and on a phone that is most of what reads well
anyway.

A failed send **raises**. The caller decides whether to fall back, because
"could not reach Telegram" and "the cycle produced nothing" must never look
the same from the outside (rule 6).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from trading_desk.adapters.telegram.client import TelegramClient
from trading_desk.domain.capture import DecisionAction, build_callback
from trading_desk.ports.clock import Clock
from trading_desk.ports.notifier import AlertLevel, DeliveryReceipt

# Telegram's documented limit. Left a little under it: the API counts UTF-16
# code units and "₹" is cheap but an emoji is not, so a text that measures 4096
# in Python can still be rejected.
MAX_MESSAGE_CHARS = 3800

_LEVEL_PREFIX = {
    AlertLevel.INFO: "",
    AlertLevel.WARNING: "WARNING: ",
    AlertLevel.CRITICAL: "CRITICAL: ",
}


def split_message(text: str, limit: int = MAX_MESSAGE_CHARS) -> list[str]:
    """Break `text` into sendable chunks, preferring section boundaries."""
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    current = ""
    for block in text.split("\n\n"):
        candidate = block if not current else f"{current}\n\n{block}"
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
        current = ""
        chunks.extend(_split_block(block, limit))
        if chunks and len(chunks[-1]) < limit:
            current = chunks.pop()
    if current:
        chunks.append(current)
    return chunks


def _split_block(block: str, limit: int) -> list[str]:
    """One section that is itself too long, cut on line boundaries."""
    if len(block) <= limit:
        return [block]
    chunks: list[str] = []
    current = ""
    for line in block.split("\n"):
        candidate = line if not current else f"{current}\n{line}"
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
        # A single line longer than the limit is cut, because the alternative
        # is not sending it at all.
        remainder = line
        while len(remainder) > limit:
            chunks.append(remainder[:limit])
            remainder = remainder[limit:]
        current = remainder
    if current:
        chunks.append(current)
    return chunks


class TelegramNotifier:
    """Implements NotifierPort against the Bot API."""

    def __init__(self, client: TelegramClient, clock: Clock) -> None:
        self._client = client
        self._clock = clock

    def send_briefing(self, markdown: str) -> DeliveryReceipt:
        """Send, splitting if needed. The receipt is for the first message.

        The first one is the one that carries the decision, so it is the one
        whose delivery is worth confirming.
        """
        receipts = [self._send(chunk) for chunk in split_message(markdown)]
        return receipts[0]

    def ask_decision(self, idea_id: UUID, markdown: str) -> DeliveryReceipt:
        keyboard = {
            "inline_keyboard": [
                [
                    {
                        "text": action.value.title(),
                        "callback_data": build_callback(action, idea_id),
                    }
                    for action in DecisionAction
                ]
            ]
        }
        return self._send(markdown, reply_markup=keyboard)

    def send_alert(self, level: AlertLevel, message: str) -> DeliveryReceipt:
        return self._send(f"{_LEVEL_PREFIX[level]}{message}")

    def _send(self, text: str, reply_markup: dict[str, Any] | None = None) -> DeliveryReceipt:
        payload: dict[str, Any] = {"chat_id": self._client.chat_id, "text": text}
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        result = self._client.call("sendMessage", payload)
        return DeliveryReceipt(
            message_id=str(result.get("message_id", "")),
            delivered_at=self._clock.utc_now(),
        )
