"""A language model that returns what the test queued, and costs nothing."""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel

from trading_desk.domain.values import Money
from trading_desk.ports.llm import LlmResult, LlmUsage


class FakeLlm:
    """Returns queued responses in order.

    Queueing real response objects rather than generating them means a test states
    exactly what the model said. A fake that invents plausible output tests the
    fake, not the code.
    """

    def __init__(self) -> None:
        self._queued: list[BaseModel] = []
        self.calls: list[tuple[str, str]] = []
        self.cost_per_call = Money.from_rupees(Decimal("0.50"))
        self._spend = Money.zero()

    def queue(self, *responses: BaseModel) -> None:
        self._queued.extend(responses)

    def complete[T: BaseModel](
        self, system_prompt: str, user_content: str, response_model: type[T], model: str
    ) -> LlmResult[T]:
        self.calls.append((model, user_content))
        if not self._queued:
            raise AssertionError(
                f"FakeLlm got an unexpected call for {response_model.__name__}. "
                "Queue a response in the test."
            )
        value = self._queued.pop(0)
        if not isinstance(value, response_model):
            raise TypeError(
                f"Queued a {type(value).__name__} but the call wanted {response_model.__name__}"
            )
        self._spend = self._spend + self.cost_per_call
        return LlmResult(
            value=value,
            usage=LlmUsage(
                model=model, input_tokens=100, output_tokens=50, cost=self.cost_per_call
            ),
        )

    def spend_this_month(self) -> Money:
        return self._spend
