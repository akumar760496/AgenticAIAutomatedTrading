"""Loading `config/llm_pricing.yaml`.

In `infra/` because it reads a file. It hands `domain.llm_cost` plain Decimals,
so the arithmetic stays pure and testable without a fixture on disk.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

from trading_desk.domain.llm_cost import LlmPricing, ModelPrice, ModelTier
from trading_desk.domain.values import Money

DEFAULT_PRICING_PATH = Path("config/llm_pricing.yaml")


def load_llm_pricing(path: Path = DEFAULT_PRICING_PATH) -> LlmPricing:
    if not path.is_file():
        raise FileNotFoundError(f"LLM pricing not found at {path}")

    raw: dict[str, Any] = yaml.safe_load(path.read_text()) or {}
    tiers: dict[ModelTier, ModelPrice] = {}
    for name, entry in (raw.get("tiers") or {}).items():
        tier = ModelTier(name)
        tiers[tier] = ModelPrice(
            model=str(entry["model"]),
            input_usd_per_million=Decimal(str(entry["input_usd_per_million"])),
            output_usd_per_million=Decimal(str(entry["output_usd_per_million"])),
        )

    missing = [tier.value for tier in ModelTier if tier not in tiers]
    if missing:
        raise ValueError(f"{path} is missing a price for tier(s): {', '.join(missing)}")

    return LlmPricing(
        tiers=tiers,
        usd_to_inr=Decimal(str(raw["usd_to_inr"])),
        monthly_cap=Money.from_rupees(Decimal(str(raw["monthly_cap_rupees"]))),
        verified=bool(raw.get("verified", False)),
        source=str(raw.get("source", "")),
    )
