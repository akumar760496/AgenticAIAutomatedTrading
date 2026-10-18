"""Loading `config/costs.yaml`.

In `infra/` rather than `domain/` because it reads a file. The rates it
produces are plain values, so `domain.costs` stays pure and testable without
a fixture on disk.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

from trading_desk.domain.costs import CostConfig, CostRates
from trading_desk.domain.values import Money

DEFAULT_COSTS_PATH = Path("config/costs.yaml")


def load_cost_config(path: Path = DEFAULT_COSTS_PATH) -> CostConfig:
    if not path.is_file():
        raise FileNotFoundError(f"Cost rates not found at {path}")

    raw: dict[str, Any] = yaml.safe_load(path.read_text()) or {}
    return CostConfig(
        rates=CostRates(
            brokerage_per_order=Money.from_rupees(Decimal(str(raw["brokerage_per_order_rupees"]))),
            stt_sell_fraction=Decimal(str(raw["stt_sell_fraction"])),
            exchange_txn_fraction=Decimal(str(raw["exchange_txn_fraction"])),
            sebi_fraction=Decimal(str(raw["sebi_fraction"])),
            stamp_duty_buy_fraction=Decimal(str(raw["stamp_duty_buy_fraction"])),
            gst_fraction=Decimal(str(raw["gst_fraction"])),
            slippage_fraction=Decimal(str(raw["slippage_fraction"])),
        ),
        verified=bool(raw.get("verified", False)),
        source=str(raw.get("source", "")),
    )
