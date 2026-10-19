"""The recorded-response client. The point of it is that the suite costs nothing.

So the test that matters is `test_a_missing_cassette_raises_rather_than_calling_out`:
a replay that silently falls through to a live model turns a free test run into
a bill, and the first you would know is the invoice.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import BaseModel

from trading_desk.adapters.llm.recorded import CassetteMissError, RecordedLlm, cassette_key
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


def _write_cassette(directory: Path, system: str, user: str, model: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{cassette_key(model, system, user)}.json"
    path.write_text(
        json.dumps(
            {
                "model": model,
                "tier": "triage",
                "input_tokens": 1_000_000,
                "output_tokens": 100_000,
                "value": {"catalyst_type": "earnings", "confidence": 0.82},
            }
        )
    )


def test_a_recorded_call_replays_without_a_network(tmp_path: Path) -> None:
    _write_cassette(tmp_path, "classify this", "Infosys beats estimates", "claude-haiku-5-5")
    client = RecordedLlm(_pricing(), directory=tmp_path)

    result = client.complete(
        system_prompt="classify this",
        user_content="Infosys beats estimates",
        response_model=Classification,
        model="claude-haiku-5-5",
    )

    assert result.value.catalyst_type == "earnings"
    # 1M input at $0.10 + 100k output at $0.50 = $0.15, at 89 = Rs 13.35
    assert result.usage.cost == Money.from_rupees(Decimal("13.35"))


def test_a_missing_cassette_raises_rather_than_calling_out(tmp_path: Path) -> None:
    client = RecordedLlm(_pricing(), directory=tmp_path)

    with pytest.raises(CassetteMissError, match="Re-record it"):
        client.complete(
            system_prompt="classify this",
            user_content="nothing recorded for this",
            response_model=Classification,
            model="claude-haiku-5-5",
        )


def test_changing_the_prompt_invalidates_the_cassette(tmp_path: Path) -> None:
    """Keying on the node name alone would keep replaying a stale answer after
    the prompt was rewritten, and the test would pass while testing nothing."""
    _write_cassette(tmp_path, "old prompt", "same input", "claude-haiku-5-5")
    client = RecordedLlm(_pricing(), directory=tmp_path)

    with pytest.raises(CassetteMissError):
        client.complete(
            system_prompt="a reworded prompt",
            user_content="same input",
            response_model=Classification,
            model="claude-haiku-5-5",
        )


def test_a_cassette_that_no_longer_matches_the_model_fails_validation(tmp_path: Path) -> None:
    """A recording made before a response model changed must not quietly
    produce a half-populated object."""
    directory = tmp_path
    directory.mkdir(parents=True, exist_ok=True)
    key = cassette_key("claude-haiku-5-5", "s", "u")
    (directory / f"{key}.json").write_text(
        json.dumps(
            {
                "model": "claude-haiku-5-5",
                "tier": "triage",
                "input_tokens": 10,
                "output_tokens": 10,
                "value": {"catalyst_type": "earnings"},  # confidence has gone missing
            }
        )
    )
    client = RecordedLlm(_pricing(), directory=directory)

    with pytest.raises(Exception, match="confidence"):
        client.complete(
            system_prompt="s",
            user_content="u",
            response_model=Classification,
            model="claude-haiku-5-5",
        )
