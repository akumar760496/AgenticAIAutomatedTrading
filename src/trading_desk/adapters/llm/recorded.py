"""A model that replays calls recorded earlier, so the suite costs nothing.

Different from `tests/fakes/llm.py`, and both are wanted. The fake returns
whatever a test queued, which is right for testing *our* logic. This replays
what a real model actually said, which is the only way to test a prompt without
paying for it every run.

Two rules make it trustworthy:

**A cassette miss raises; it never falls through to the network.** A replay that
quietly makes a real call turns a free test run into a bill, and the first you
would know is the invoice.

**The match key includes the prompt.** Keying on the node name alone would keep
replaying a stale answer after the prompt had been rewritten, and the test would
pass while testing nothing.

Record a cassette with `RecordedLlm.capture(...)` wrapped around a real client,
then commit the JSON. Cassettes hold model output, not credentials (rule 8).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from trading_desk.domain.call_identity import call_key
from trading_desk.domain.llm_cost import LlmPricing, ModelTier, cost_of_call
from trading_desk.ports.llm import LlmPort, LlmResult, LlmUsage

DEFAULT_CASSETTE_DIR = Path("tests/cassettes/llm")


class CassetteMissError(RuntimeError):
    """No recording for this call. Never falls back to a live model."""


def cassette_key(model: str, system_prompt: str, user_content: str) -> str:
    """A short, stable name for one call, shared with the cycle response cache.

    Kept as a name here because the cassette files on disk are named by it, and
    because `cassette_key` is what the recording workflow talks about. The
    definition itself lives in the domain so the two callers cannot drift.
    """
    return call_key(model, system_prompt, user_content)


class RecordedLlm:
    """Replays recorded responses from disk."""

    def __init__(self, pricing: LlmPricing, directory: Path = DEFAULT_CASSETTE_DIR) -> None:
        self._pricing = pricing
        self._directory = directory
        self.replayed: list[str] = []

    def complete[T: BaseModel](
        self, system_prompt: str, user_content: str, response_model: type[T], model: str
    ) -> LlmResult[T]:
        key = cassette_key(model, system_prompt, user_content)
        path = self._directory / f"{key}.json"
        if not path.is_file():
            raise CassetteMissError(
                f"No cassette at {path} for a {response_model.__name__} call to {model}. "
                "Re-record it rather than letting the test call a live model."
            )

        recorded: dict[str, Any] = json.loads(path.read_text())
        self.replayed.append(key)
        value = response_model.model_validate(recorded["value"])
        input_tokens = int(recorded["input_tokens"])
        output_tokens = int(recorded["output_tokens"])
        tier = ModelTier(recorded["tier"])
        return LlmResult(
            value=value,
            usage=LlmUsage(
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                # Recomputed rather than read from the cassette, so a price
                # change is reflected the moment config changes.
                cost=cost_of_call(self._pricing, tier, input_tokens, output_tokens),
            ),
        )

    def capture[T: BaseModel](
        self,
        live: LlmPort,
        tier: ModelTier,
        system_prompt: str,
        user_content: str,
        response_model: type[T],
    ) -> LlmResult[T]:
        """Make one real call and write it to disk. Costs real money; run by hand."""
        model = self._pricing.model_for(tier)
        result = live.complete(
            system_prompt=system_prompt,
            user_content=user_content,
            response_model=response_model,
            model=model,
        )
        self._directory.mkdir(parents=True, exist_ok=True)
        path = self._directory / f"{cassette_key(model, system_prompt, user_content)}.json"
        path.write_text(
            json.dumps(
                {
                    "model": model,
                    "tier": tier.value,
                    "input_tokens": result.usage.input_tokens,
                    "output_tokens": result.usage.output_tokens,
                    "value": result.value.model_dump(mode="json"),
                },
                indent=2,
            )
        )
        return result
