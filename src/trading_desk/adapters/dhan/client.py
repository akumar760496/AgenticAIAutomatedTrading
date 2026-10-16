"""HTTP client for DhanHQ v2.

Two things make this more than a thin httpx wrapper:

1. **The option chain endpoint allows one request every 3 seconds.** Not a
   burst budget - a hard spacing rule. Exceeding it gets requests rejected, so the
   client spaces them itself rather than relying on callers to remember.
2. **Tokens are short-lived and come from several places.** How a token is obtained
   is a decision that is still open (a 24-hour token pasted into .env, a browser
   consent flow, or PIN+TOTP for unattended use), so the client takes a token
   provider rather than a token.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import httpx

BASE_URL = "https://api.dhan.co/v2"

# Dhan documents: "Rate limit for Option Chain API is set to one unique request
# every 3 seconds."
#
# 3.2s was not enough. A live 26-instrument run on 2026-10-16 got HTTP 429 on two
# of them - the documented limit appears to be a floor rather than a guarantee,
# and the expiry-list calls share the same budget. 4.5s plus retry-on-429 ran
# clean. The cost is ~4 minutes for a full run, which is irrelevant for a job that
# fires three times a day.
OPTION_CHAIN_MIN_INTERVAL_SECONDS = 4.5

# Retry only on 429. Everything else - a bad instrument, an expired token - fails
# immediately, because retrying those just wastes the rate-limit budget.
RATE_LIMIT_STATUS = 429
RATE_LIMIT_BACKOFF_SECONDS = (10.0, 30.0, 60.0)

DEFAULT_TIMEOUT_SECONDS = 20.0


class DhanApiError(RuntimeError):
    """A Dhan request failed. Carries the status and body, never the token."""


class RateLimiter:
    """Spaces calls so that consecutive requests are at least `interval` apart.

    Deliberately the simplest thing that works: remember the last call, sleep for
    the remainder. No token bucket, no threading - the snapshotter is a single
    sequential job and a bucket would only hide the 3-second floor.
    """

    def __init__(
        self, interval_seconds: float, sleep: Callable[[float], None] = time.sleep
    ) -> None:
        self._interval = interval_seconds
        self._sleep = sleep
        self._last_call_at: float | None = None
        self.total_waited_seconds = 0.0

    def wait(self, now: float | None = None) -> None:
        current = time.monotonic() if now is None else now
        if self._last_call_at is not None:
            elapsed = current - self._last_call_at
            remaining = self._interval - elapsed
            if remaining > 0:
                self._sleep(remaining)
                self.total_waited_seconds += remaining
                current += remaining
        self._last_call_at = current


class DhanClient:
    """Authenticated, rate-limited access to the Dhan REST API."""

    def __init__(
        self,
        client_id: str,
        token_provider: Callable[[], str],
        http_client: httpx.Client | None = None,
        rate_limiter: RateLimiter | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._sleep = sleep
        self._client_id = client_id
        self._token_provider = token_provider
        self._http = http_client or httpx.Client(base_url=BASE_URL, timeout=DEFAULT_TIMEOUT_SECONDS)
        self._rate_limiter = rate_limiter or RateLimiter(OPTION_CHAIN_MIN_INTERVAL_SECONDS)

    def _headers(self) -> dict[str, str]:
        return {
            "access-token": self._token_provider(),
            "client-id": self._client_id,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def post(self, path: str, payload: dict[str, Any], rate_limited: bool = True) -> dict[str, Any]:
        """POST and return the parsed body, or raise DhanApiError.

        Retries on 429 and nothing else. The error message carries the status and
        Dhan's own text, which is how an expired token is told apart from a bad
        instrument id, and never the request headers, which carry the token.
        """
        last_error = "no attempt made"

        for attempt, backoff in enumerate((0.0, *RATE_LIMIT_BACKOFF_SECONDS)):
            if backoff:
                self._sleep(backoff)
            if rate_limited:
                self._rate_limiter.wait()

            try:
                response = self._http.post(path, json=payload, headers=self._headers())
            except httpx.HTTPError as error:
                raise DhanApiError(f"{path}: request failed ({type(error).__name__})") from error

            if response.status_code == RATE_LIMIT_STATUS:
                last_error = f"HTTP 429 after {attempt + 1} attempt(s) - {response.text[:120]}"
                continue

            if response.status_code != httpx.codes.OK:
                raise DhanApiError(f"{path}: HTTP {response.status_code} - {response.text[:200]}")

            body: dict[str, Any] = response.json()
            if body.get("status") == "failure":
                raise DhanApiError(f"{path}: Dhan returned failure - {body.get('remarks')}")
            return body

        raise DhanApiError(f"{path}: {last_error}")

    def close(self) -> None:
        self._http.close()
