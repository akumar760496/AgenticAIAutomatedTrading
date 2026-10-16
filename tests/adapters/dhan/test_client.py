"""The rate limiter spaces requests, and errors never leak the token."""

from __future__ import annotations

import httpx
import pytest

from trading_desk.adapters.dhan.client import (
    OPTION_CHAIN_MIN_INTERVAL_SECONDS,
    RATE_LIMIT_BACKOFF_SECONDS,
    DhanApiError,
    DhanClient,
    RateLimiter,
)
from trading_desk.adapters.dhan.token import StaticTokenProvider

SECRET_TOKEN = "live-token-value-that-must-never-appear"


class RecordingSleeper:
    """Captures sleep durations so the spacing can be asserted without waiting."""

    def __init__(self) -> None:
        self.sleeps: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.sleeps.append(seconds)


class TestRateLimiter:
    def test_first_call_does_not_wait(self) -> None:
        sleeper = RecordingSleeper()
        limiter = RateLimiter(3.2, sleep=sleeper)

        limiter.wait(now=100.0)

        assert sleeper.sleeps == []

    def test_a_fast_second_call_waits_for_the_remainder(self) -> None:
        """Dhan allows one option chain request every 3 seconds."""
        sleeper = RecordingSleeper()
        limiter = RateLimiter(3.2, sleep=sleeper)

        limiter.wait(now=100.0)
        limiter.wait(now=101.0)  # only 1s later

        assert sleeper.sleeps == [pytest.approx(2.2)]

    def test_a_slow_second_call_does_not_wait(self) -> None:
        sleeper = RecordingSleeper()
        limiter = RateLimiter(3.2, sleep=sleeper)

        limiter.wait(now=100.0)
        limiter.wait(now=110.0)

        assert sleeper.sleeps == []

    def test_the_interval_has_margin_over_dhans_three_seconds(self) -> None:
        """The server measures arrival time, so exactly 3.0 would be a coin flip."""
        assert OPTION_CHAIN_MIN_INTERVAL_SECONDS > 3.0


class TestClientErrors:
    def _client(
        self, handler: httpx.MockTransport, sleeper: RecordingSleeper | None = None
    ) -> DhanClient:
        return DhanClient(
            client_id="1000000001",
            token_provider=StaticTokenProvider(SECRET_TOKEN),
            http_client=httpx.Client(transport=handler, base_url="https://api.dhan.co/v2"),
            rate_limiter=RateLimiter(0.0, sleep=RecordingSleeper()),
            sleep=sleeper or RecordingSleeper(),
        )

    def test_an_http_error_raises_without_the_token(self) -> None:
        transport = httpx.MockTransport(lambda request: httpx.Response(401, text="Invalid token"))

        with pytest.raises(DhanApiError) as caught:
            self._client(transport).post("/optionchain", {})

        assert "401" in str(caught.value)
        assert SECRET_TOKEN not in str(caught.value)

    def test_a_dhan_failure_body_raises(self) -> None:
        """HTTP 200 with status=failure is Dhan's way of reporting a bad request."""
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200, json={"status": "failure", "remarks": "Invalid UnderlyingScrip"}
            )
        )

        with pytest.raises(DhanApiError, match="Invalid UnderlyingScrip"):
            self._client(transport).post("/optionchain", {})

    def test_the_token_is_sent_in_the_access_token_header(self) -> None:
        seen: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen.update(request.headers)
            return httpx.Response(200, json={"status": "success", "data": {}})

        self._client(httpx.MockTransport(handler)).post("/optionchain", {})

        assert seen["access-token"] == SECRET_TOKEN
        assert seen["client-id"] == "1000000001"


class TestTokenProvider:
    def test_an_empty_token_is_rejected_with_instructions(self) -> None:
        with pytest.raises(ValueError, match=r"web\.dhan\.co"):
            StaticTokenProvider("")

    def test_repr_never_shows_the_token(self) -> None:
        provider = StaticTokenProvider(SECRET_TOKEN)

        assert SECRET_TOKEN not in repr(provider)
        assert "***" in repr(provider)


class TestRateLimitRetry:
    """A live 26-instrument run hit 429 twice at 3.2s spacing. Hence both fixes."""

    def _client(self, handler: httpx.MockTransport, sleeper: RecordingSleeper) -> DhanClient:
        return DhanClient(
            client_id="1000000001",
            token_provider=StaticTokenProvider(SECRET_TOKEN),
            http_client=httpx.Client(transport=handler, base_url="https://api.dhan.co/v2"),
            rate_limiter=RateLimiter(0.0, sleep=RecordingSleeper()),
            sleep=sleeper,
        )

    def test_a_429_is_retried_and_can_succeed(self) -> None:
        attempts = {"count": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            attempts["count"] += 1
            if attempts["count"] == 1:
                return httpx.Response(429, text='{"data":{"805":"Too many requests"}}')
            return httpx.Response(200, json={"status": "success", "data": {"ok": True}})

        sleeper = RecordingSleeper()
        body = self._client(httpx.MockTransport(handler), sleeper).post("/optionchain", {})

        assert body["data"]["ok"] is True
        assert sleeper.sleeps == [RATE_LIMIT_BACKOFF_SECONDS[0]]

    def test_repeated_429s_eventually_raise(self) -> None:
        transport = httpx.MockTransport(lambda request: httpx.Response(429, text="limit"))
        sleeper = RecordingSleeper()

        with pytest.raises(DhanApiError, match="429"):
            self._client(transport, sleeper).post("/optionchain", {})

        assert len(sleeper.sleeps) == len(RATE_LIMIT_BACKOFF_SECONDS)

    def test_other_errors_are_not_retried(self) -> None:
        """Retrying a bad token or a bad instrument just burns the rate-limit budget."""
        transport = httpx.MockTransport(lambda request: httpx.Response(401, text="bad token"))
        sleeper = RecordingSleeper()

        with pytest.raises(DhanApiError, match="401"):
            self._client(transport, sleeper).post("/optionchain", {})

        assert sleeper.sleeps == []

    def test_the_interval_was_widened_after_the_live_429s(self) -> None:
        assert OPTION_CHAIN_MIN_INTERVAL_SECONDS >= 4.0
