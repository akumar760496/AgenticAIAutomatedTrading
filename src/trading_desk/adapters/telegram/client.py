"""One HTTP client for the Telegram Bot API.

**The bot token is in the URL**, which is the single most important thing about
this module. Telegram puts the credential in the path - `/bot<token>/sendMessage`
- so any log line, exception or traceback that echoes the URL leaks it
(CLAUDE.md rule 8). Nothing here ever includes the URL in an error; errors name
the *method* instead, which is what you actually want when debugging anyway.

Retries cover transport failures, 429 and 5xx. A 400 is not retried: Telegram
returns it for a message that is too long, a bad chat id or malformed markup,
and sending the same bad request four times turns one visible failure into four
invisible ones.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from trading_desk.infra.logging import redact

API_ROOT = "https://api.telegram.org"

MAX_ATTEMPTS = 3
BACKOFF_SECONDS = (1.0, 4.0)
DEFAULT_TIMEOUT_SECONDS = 15.0

# Headroom added to a long poll's own timeout before giving up on the socket.
# `getUpdates` asks Telegram to hold the connection for `timeout` seconds, so the
# HTTP read timeout must be the larger of the two or an idle poll can never
# succeed - it times out locally while the server is still waiting, exactly as
# designed. Measured on a live run: a 15s client timeout against a 25s long poll
# failed every single time there was nothing queued, which is the normal state.
LONG_POLL_MARGIN_SECONDS = 10.0

TOO_MANY_REQUESTS = 429
SERVER_ERROR_FLOOR = 500


class TelegramError(RuntimeError):
    """A Telegram call failed. Never carries the URL - see the module docstring."""


class TelegramClient:
    def __init__(
        self,
        bot_token: str,
        chat_id: str,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        sleep: Any = time.sleep,
    ) -> None:
        self._token = bot_token
        self.chat_id = chat_id
        self._timeout = timeout_seconds
        self._client = httpx.Client(timeout=timeout_seconds)
        self._sleep = sleep

    def call(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        """One API call, with retries. Raises `TelegramError` if it never lands."""
        last_error = ""
        for attempt in range(MAX_ATTEMPTS):
            try:
                response = self._client.post(
                    self._url(method), json=payload, timeout=_timeout_for(payload, self._timeout)
                )
            except httpx.HTTPError as error:
                last_error = f"{type(error).__name__}"
            else:
                if response.status_code < TOO_MANY_REQUESTS:
                    return self._body(method, response)
                if response.status_code != TOO_MANY_REQUESTS and (
                    response.status_code < SERVER_ERROR_FLOOR
                ):
                    raise TelegramError(
                        f"{method} refused: HTTP {response.status_code} - "
                        f"{redact(_description(response))}"
                    )
                last_error = f"HTTP {response.status_code}"

            if attempt < len(BACKOFF_SECONDS):
                self._sleep(BACKOFF_SECONDS[attempt])

        raise TelegramError(f"{method} failed after {MAX_ATTEMPTS} attempts: {last_error}")

    def _url(self, method: str) -> str:
        return f"{API_ROOT}/bot{self._token}/{method}"

    @staticmethod
    def _body(method: str, response: httpx.Response) -> dict[str, Any]:
        if response.status_code >= httpx.codes.BAD_REQUEST:
            # Telegram puts the actual reason in `description`, and discarding it
            # cost a real debugging session: "answerCallbackQuery refused: HTTP
            # 400" says nothing, while the body said "query is too old and
            # response timeout expired or query ID is invalid" - which names the
            # cause and the fix. Redacted, because an error body is still a log
            # line (rule 8).
            raise TelegramError(
                f"{method} refused: HTTP {response.status_code} - {redact(_description(response))}"
            )
        body: dict[str, Any] = response.json()
        if not body.get("ok", False):
            raise TelegramError(f"{method} returned not-ok: {body.get('description', '?')}")
        result: dict[str, Any] = body.get("result", {})
        return result if isinstance(result, dict) else {"result": result}

    def call_list(self, method: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
        """Same call, for the methods that return an array (`getUpdates`)."""
        body = self.call(method, payload)
        items = body.get("result", body)
        return list(items) if isinstance(items, list) else []

    def close(self) -> None:
        self._client.close()


def _timeout_for(payload: dict[str, Any], default: float) -> float:
    """How long to wait on the socket for this call.

    A long poll carries its own `timeout`, and the socket must outlast it. Every
    other call keeps the default. Deriving it from the payload rather than
    raising the global default means the two numbers cannot drift apart again:
    whatever `POLL_SECONDS` becomes, this follows it.
    """
    poll = payload.get("timeout")
    if isinstance(poll, int | float):
        return max(default, float(poll) + LONG_POLL_MARGIN_SECONDS)
    return default


def _description(response: httpx.Response) -> str:
    """Telegram's own explanation, or a stand-in when the body is not JSON.

    A non-JSON body happens on a gateway error, where the proxy answers instead
    of Telegram. Returning the status text keeps the caller's message useful
    rather than raising a second error while reporting the first.
    """
    try:
        body = response.json()
    except ValueError:
        return response.text[:200] or "no body"
    if isinstance(body, dict):
        return str(body.get("description", "no description"))
    return "no description"
