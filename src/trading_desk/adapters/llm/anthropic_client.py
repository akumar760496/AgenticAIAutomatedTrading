"""Anthropic, returning validated objects.

**Structured output is done with a forced tool call, not by asking for JSON.**
Asking a model for JSON in the prompt gets JSON most of the time, and the rest
of the time gets JSON wrapped in an apology. Declaring the Pydantic schema as a
tool and setting `tool_choice` to that tool makes the shape the API's problem
rather than the prompt's, and a response that still does not validate raises.

Retries cover transport and rate limits only. **A response that fails validation
is not retried**: the same prompt produces the same shape, so a retry burns
money to receive the same malformed answer, and the cycle should fail (rule 6).
"""

from __future__ import annotations

import time
from typing import Any, cast

import anthropic
from anthropic.types import MessageParam, ToolParam
from pydantic import BaseModel, ValidationError

from trading_desk.domain.llm_cost import LlmPricing, ModelTier, cost_of_call
from trading_desk.ports.llm import LlmResult, LlmUsage

MAX_ATTEMPTS = 3
BACKOFF_SECONDS = (1.0, 4.0)
DEFAULT_MAX_TOKENS = 4096
RESULT_TOOL = "record_result"


class LlmCallError(RuntimeError):
    """The model could not be reached, or did not answer in the required shape."""


class AnthropicLlm:
    """One model call at a time. No batching, no streaming - v1 has neither."""

    def __init__(
        self,
        api_key: str,
        pricing: LlmPricing,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        client: Any | None = None,
    ) -> None:
        # `client` is injected in tests so nothing constructs a real one there.
        self._client = client if client is not None else anthropic.Anthropic(api_key=api_key)
        self._pricing = pricing
        self._max_tokens = max_tokens

    def complete[T: BaseModel](
        self, system_prompt: str, user_content: str, response_model: type[T], model: str
    ) -> LlmResult[T]:
        response = self._call_with_retries(system_prompt, user_content, response_model, model)
        payload = self._extract_tool_input(response, response_model.__name__)

        try:
            value = response_model.model_validate(payload)
        except ValidationError as error:
            raise LlmCallError(
                f"{model} returned a {response_model.__name__} that does not validate: {error}"
            ) from error

        return LlmResult(value=value, usage=self._usage(response, model))

    def _call_with_retries[T: BaseModel](
        self, system_prompt: str, user_content: str, response_model: type[T], model: str
    ) -> Any:
        # cast rather than build the SDK's TypedDicts by hand: the schema comes
        # straight from Pydantic and is the right shape, but mypy cannot see that
        # through model_json_schema()'s dict[str, Any] return.
        tool = cast(
            "ToolParam",
            {
                "name": RESULT_TOOL,
                "description": f"Return the result as a {response_model.__name__}.",
                "input_schema": response_model.model_json_schema(),
            },
        )
        messages: list[MessageParam] = [{"role": "user", "content": user_content}]

        last_error: Exception | None = None
        for attempt in range(MAX_ATTEMPTS):
            try:
                return self._client.messages.create(
                    model=model,
                    max_tokens=self._max_tokens,
                    system=system_prompt,
                    messages=messages,
                    tools=[tool],
                    tool_choice={"type": "tool", "name": RESULT_TOOL},
                )
            except (anthropic.APIConnectionError, anthropic.RateLimitError) as error:
                last_error = error
                if attempt < len(BACKOFF_SECONDS):
                    time.sleep(BACKOFF_SECONDS[attempt])

        raise LlmCallError(f"{model} unreachable after {MAX_ATTEMPTS} attempts: {last_error}")

    def _extract_tool_input(self, response: Any, model_name: str) -> dict[str, Any]:
        for block in response.content:
            if getattr(block, "type", None) == "tool_use" and block.name == RESULT_TOOL:
                return dict(block.input)
        raise LlmCallError(
            f"No {model_name} in the response - the model answered in prose despite "
            "being given a forced tool call. Treated as a failure, not parsed."
        )

    def _usage(self, response: Any, model: str) -> LlmUsage:
        """Cost comes from the same domain function the cap uses.

        Deliberately not from anything the API returns: one arithmetic path
        means the ledger and the cap can never disagree about what a call cost.
        """
        tier = self._tier_for(model)
        input_tokens = int(response.usage.input_tokens)
        output_tokens = int(response.usage.output_tokens)
        return LlmUsage(
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost=cost_of_call(self._pricing, tier, input_tokens, output_tokens),
        )

    def _tier_for(self, model: str) -> ModelTier:
        for tier, price in self._pricing.tiers.items():
            if price.model == model:
                return tier
        raise LlmCallError(f"No configured tier uses model {model}; cost cannot be computed")
