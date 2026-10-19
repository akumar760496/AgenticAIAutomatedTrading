"""The Anthropic adapter, with the SDK client injected so nothing leaves the box.

Two behaviours are worth more than the rest: a prose answer is a failure rather
than something to parse, and a response that fails validation is **not retried**
- retrying it spends money to receive the same malformed answer.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import anthropic
import httpx2
import pytest
from pydantic import BaseModel

from trading_desk.adapters.llm.anthropic_client import RESULT_TOOL, AnthropicLlm, LlmCallError
from trading_desk.domain.llm_cost import LlmPricing, ModelPrice, ModelTier
from trading_desk.domain.values import Money


class Classification(BaseModel):
    catalyst_type: str
    confidence: float


def _pricing() -> LlmPricing:
    return LlmPricing(
        tiers={
            ModelTier.TRIAGE: ModelPrice(
                model="claude-haiku-5-5",
                input_usd_per_million=Decimal("0.10"),
                output_usd_per_million=Decimal("0.50"),
            ),
            ModelTier.SYNTHESIS: ModelPrice(
                model="claude-opus-5",
                input_usd_per_million=Decimal("5.00"),
                output_usd_per_million=Decimal("25.00"),
            ),
        },
        usd_to_inr=Decimal("89"),
        monthly_cap=Money.from_rupees(Decimal("2000")),
        verified=False,
        source="test",
    )


class _Block:
    def __init__(self, block_type: str, name: str = "", payload: dict[str, Any] | None = None):
        self.type = block_type
        self.name = name
        self.input = payload or {}


class _Usage:
    def __init__(self, input_tokens: int, output_tokens: int):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _Response:
    def __init__(self, content: list[_Block], input_tokens: int = 1000, output_tokens: int = 200):
        self.content = content
        self.usage = _Usage(input_tokens, output_tokens)


class _Messages:
    """Stands in for `client.messages`, recording what it was asked."""

    def __init__(self, responses: list[Any]) -> None:
        self._responses = responses
        self.requests: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.requests.append(kwargs)
        outcome = self._responses.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class _Client:
    def __init__(self, responses: list[Any]) -> None:
        self.messages = _Messages(responses)


def _adapter(responses: list[Any]) -> tuple[AnthropicLlm, _Client]:
    client = _Client(responses)
    return AnthropicLlm(api_key="unused", pricing=_pricing(), client=client), client


def test_a_tool_use_response_is_parsed_and_priced() -> None:
    good = _Response(
        [_Block("tool_use", RESULT_TOOL, {"catalyst_type": "earnings", "confidence": 0.9})],
        input_tokens=1_000_000,
        output_tokens=100_000,
    )
    adapter, _ = _adapter([good])

    result = adapter.complete("system", "user", Classification, "claude-haiku-5-5")

    assert result.value.catalyst_type == "earnings"
    assert result.usage.cost == Money.from_rupees(Decimal("13.35"))


def test_the_schema_is_sent_as_a_forced_tool_call() -> None:
    """Asking for JSON in the prompt gets JSON most of the time. Forcing the
    tool makes the shape the API's problem rather than the prompt's."""
    good = _Response([_Block("tool_use", RESULT_TOOL, {"catalyst_type": "x", "confidence": 0.1})])
    adapter, client = _adapter([good])

    adapter.complete("system", "user", Classification, "claude-haiku-5-5")

    request = client.messages.requests[0]
    assert request["tool_choice"] == {"type": "tool", "name": RESULT_TOOL}
    assert "confidence" in request["tools"][0]["input_schema"]["properties"]


def test_a_prose_answer_is_a_failure_not_something_to_parse() -> None:
    adapter, _ = _adapter([_Response([_Block("text")])])

    with pytest.raises(LlmCallError, match="answered in prose"):
        adapter.complete("system", "user", Classification, "claude-haiku-5-5")


def test_a_response_that_fails_validation_is_not_retried() -> None:
    """The same prompt returns the same shape, so a retry buys nothing and
    costs the call again."""
    bad = _Response([_Block("tool_use", RESULT_TOOL, {"catalyst_type": "earnings"})])
    adapter, client = _adapter([bad])

    with pytest.raises(LlmCallError, match="does not validate"):
        adapter.complete("system", "user", Classification, "claude-haiku-5-5")

    assert len(client.messages.requests) == 1


def test_a_rate_limit_is_retried_and_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("trading_desk.adapters.llm.anthropic_client.time.sleep", lambda _: None)
    limited = anthropic.RateLimitError("slow down", response=_http_response(429), body=None)
    good = _Response([_Block("tool_use", RESULT_TOOL, {"catalyst_type": "x", "confidence": 0.5})])
    adapter, client = _adapter([limited, good])

    result = adapter.complete("system", "user", Classification, "claude-haiku-5-5")

    assert result.value.catalyst_type == "x"
    assert len(client.messages.requests) == 2


def test_an_unreachable_api_gives_up_rather_than_looping(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("trading_desk.adapters.llm.anthropic_client.time.sleep", lambda _: None)
    failures = [anthropic.APIConnectionError(request=_http_request()) for _ in range(3)]
    adapter, client = _adapter(failures)

    with pytest.raises(LlmCallError, match="unreachable after 3 attempts"):
        adapter.complete("system", "user", Classification, "claude-haiku-5-5")

    assert len(client.messages.requests) == 3


def test_a_model_no_tier_prices_cannot_be_costed() -> None:
    """Silently costing an unpriced model at zero is how spend escapes the cap."""
    good = _Response([_Block("tool_use", RESULT_TOOL, {"catalyst_type": "x", "confidence": 0.5})])
    adapter, _ = _adapter([good])

    with pytest.raises(LlmCallError, match="No configured tier"):
        adapter.complete("system", "user", Classification, "some-unlisted-model")


# The SDK's error types want genuine request/response objects, so build real
# ones rather than stand-ins - a stand-in here would only test our own duck
# typing. Note `httpx2`, not `httpx`: the anthropic SDK ships against the newer
# package, and the two are different types to mypy even though they look alike.
def _http_request() -> httpx2.Request:
    return httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


def _http_response(status_code: int) -> httpx2.Response:
    return httpx2.Response(status_code, request=_http_request())
