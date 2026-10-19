"""The Telegram adapters, against a client that records instead of sending.

Two things are worth testing here and neither is "does httpx work": that a
long briefing is split on a sensible boundary, and that **the bot token never
appears in an error message**. The token is in the URL path, so any code that
echoes a URL leaks a credential (CLAUDE.md rule 8).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import httpx
import pytest

from tests.fakes import FrozenClock
from trading_desk.adapters.telegram.chat import TelegramChat, _to_update
from trading_desk.adapters.telegram.client import TelegramClient, TelegramError
from trading_desk.adapters.telegram.notifier import (
    MAX_MESSAGE_CHARS,
    TelegramNotifier,
    split_message,
)
from trading_desk.domain.capture import DecisionAction, parse_callback
from trading_desk.ports.chat import UpdateKind
from trading_desk.ports.notifier import AlertLevel

NOW = datetime(2026, 10, 19, 4, 0, tzinfo=UTC)
IDEA = UUID("11111111-2222-3333-4444-555555555555")
TOKEN = "123456:THIS-IS-A-SECRET-TOKEN"


class RecordingClient:
    """Stands in for TelegramClient, keeping what would have been sent."""

    def __init__(self) -> None:
        self.chat_id = "42"
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def call(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((method, payload))
        return {"message_id": len(self.calls)}

    def call_list(self, method: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
        self.calls.append((method, payload))
        return []


class TestSplitting:
    def test_a_short_briefing_is_one_message(self) -> None:
        assert split_message("short") == ["short"]

    def test_a_long_briefing_splits_between_sections(self) -> None:
        text = "SECTION A\n" + "x" * 90 + "\n\nSECTION B\n" + "y" * 90

        chunks = split_message(text, limit=120)

        assert len(chunks) == 2
        assert chunks[0].startswith("SECTION A")
        assert chunks[1].startswith("SECTION B")

    def test_no_chunk_exceeds_the_limit(self) -> None:
        text = "\n\n".join(f"SECTION {index}\n" + "z" * 200 for index in range(10))

        assert all(len(chunk) <= 300 for chunk in split_message(text, limit=300))

    def test_a_single_overlong_line_is_cut_rather_than_dropped(self) -> None:
        chunks = split_message("q" * 500, limit=150)

        assert "".join(chunks) == "q" * 500

    def test_the_default_limit_is_under_telegram_s(self) -> None:
        """Telegram counts UTF-16 units, Python counts characters, and the
        briefing is full of ₹. The margin is the point."""
        assert MAX_MESSAGE_CHARS < 4096


class TestTheNotifier:
    def test_a_long_briefing_sends_several_messages_and_receipts_the_first(self) -> None:
        client = RecordingClient()
        notifier = TelegramNotifier(client, FrozenClock(NOW))  # type: ignore[arg-type]

        receipt = notifier.send_briefing("\n\n".join("x" * 1000 for _ in range(6)))

        assert len(client.calls) > 1
        assert receipt.message_id == "1"

    def test_no_parse_mode_is_set(self) -> None:
        """MarkdownV2 escaping on text full of ₹ . ( ) - is a 400 waiting to
        happen, and a 400 is a briefing that did not arrive."""
        client = RecordingClient()
        TelegramNotifier(client, FrozenClock(NOW)).send_briefing("₹10,500.00 (proxy) 0.52 x ATR")  # type: ignore[arg-type]

        assert "parse_mode" not in client.calls[0][1]

    def test_asking_for_a_decision_offers_the_three_actions(self) -> None:
        client = RecordingClient()
        TelegramNotifier(client, FrozenClock(NOW)).ask_decision(IDEA, "RELIANCE #1")  # type: ignore[arg-type]

        keyboard = client.calls[0][1]["reply_markup"]["inline_keyboard"][0]
        assert [button["text"] for button in keyboard] == ["Took", "Skipped", "Modified"]

    def test_the_button_data_round_trips_to_the_idea(self) -> None:
        client = RecordingClient()
        TelegramNotifier(client, FrozenClock(NOW)).ask_decision(IDEA, "RELIANCE #1")  # type: ignore[arg-type]

        first = client.calls[0][1]["reply_markup"]["inline_keyboard"][0][0]
        pending = parse_callback(first["callback_data"])
        assert pending.idea_id == IDEA
        assert pending.action is DecisionAction.TOOK

    def test_an_alert_carries_its_level(self) -> None:
        client = RecordingClient()
        TelegramNotifier(client, FrozenClock(NOW)).send_alert(AlertLevel.CRITICAL, "feed down")  # type: ignore[arg-type]

        assert client.calls[0][1]["text"].startswith("CRITICAL: ")


class TestTheChat:
    def test_the_offset_asks_for_the_next_update_not_the_same_one(self) -> None:
        """Telegram treats the offset as an acknowledgement of everything
        before it, so an off-by-one here replays or loses a decision."""
        client = RecordingClient()
        TelegramChat(client).poll(after_update_id=7, timeout_seconds=25)  # type: ignore[arg-type]

        assert client.calls[0][1]["offset"] == 8

    def test_a_reason_prompt_forces_a_reply(self) -> None:
        client = RecordingClient()
        TelegramChat(client).ask_for_reason("why?")  # type: ignore[arg-type]

        assert client.calls[0][1]["reply_markup"] == {"force_reply": True}

    def test_a_tapped_button_becomes_a_button_update(self) -> None:
        update = _to_update(
            {
                "update_id": 3,
                "callback_query": {
                    "id": "cb-1",
                    "data": "d:TOOK:xyz",
                    "message": {"date": 1760000000},
                },
            }
        )

        assert update.kind is UpdateKind.BUTTON
        assert update.callback_data == "d:TOOK:xyz"
        assert update.received_at.tzinfo is not None

    def test_a_reply_carries_the_quoted_text(self) -> None:
        update = _to_update(
            {
                "update_id": 4,
                "message": {
                    "date": 1760000000,
                    "text": "took it",
                    "reply_to_message": {"text": "[TOOK 1111]"},
                },
            }
        )

        assert update.kind is UpdateKind.MESSAGE
        assert update.quoted_text == "[TOOK 1111]"

    def test_an_update_we_do_not_handle_still_comes_back(self) -> None:
        """So the cursor can advance past it. An update nobody handles must
        not become an update nobody acknowledges."""
        update = _to_update({"update_id": 9, "edited_message": {}})

        assert update.kind is UpdateKind.OTHER
        assert update.update_id == 9


class TestTheTokenNeverLeaks:
    def test_a_refusal_names_the_method_not_the_url(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            del request
            return httpx.Response(400, json={"ok": False, "description": "bad chat id"})

        client = TelegramClient(TOKEN, "42", sleep=lambda _: None)
        client._client = httpx.Client(transport=httpx.MockTransport(handler))

        with pytest.raises(TelegramError) as error:
            client.call("sendMessage", {})

        assert TOKEN not in str(error.value)
        assert "sendMessage" in str(error.value)

    def test_a_transport_failure_after_retries_names_no_url(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            del request
            raise httpx.ConnectError("no route")

        client = TelegramClient(TOKEN, "42", sleep=lambda _: None)
        client._client = httpx.Client(transport=httpx.MockTransport(handler))

        with pytest.raises(TelegramError) as error:
            client.call("getUpdates", {})

        assert TOKEN not in str(error.value)

    def test_a_429_is_retried_and_then_succeeds(self) -> None:
        attempts = {"count": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            del request
            attempts["count"] += 1
            if attempts["count"] == 1:
                return httpx.Response(429, json={"ok": False})
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 5}})

        client = TelegramClient(TOKEN, "42", sleep=lambda _: None)
        client._client = httpx.Client(transport=httpx.MockTransport(handler))

        assert client.call("sendMessage", {})["message_id"] == 5
        expected_attempts = 2
        assert attempts["count"] == expected_attempts
