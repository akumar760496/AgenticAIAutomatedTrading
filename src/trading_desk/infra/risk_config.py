"""Reading the account figures out of config/risk.yaml.

`equity` is `None` when the file leaves it blank, and that absence travels all
the way to the briefing rather than being filled in with something reasonable.
A default equity would be a number nobody chose silently deciding how large a
position looks - the single worst place in this system for a plausible guess
(CLAUDE.md rule 6, ISS-022).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

from trading_desk.domain.values import Money

DEFAULT_RISK_PATH = Path("config/risk.yaml")


@dataclass(frozen=True)
class RiskConfig:
    """Account size and portfolio limits, or an honest absence."""

    equity: Money | None
    max_symbol_exposure_fraction: float
    max_portfolio_heat_fraction: float
    daily_loss_limit_fraction: float
    max_trades_per_day: int
    verified: bool
    source: str

    @property
    def has_capital_figure(self) -> bool:
        return self.equity is not None

    def missing_reason(self) -> str:
        return (
            "no capital figure in config/risk.yaml (ISS-022) - set equity_rupees "
            "and the briefing starts showing a hypothetical size"
        )


def load_risk_config(path: Path = DEFAULT_RISK_PATH) -> RiskConfig:
    raw: dict[str, Any] = yaml.safe_load(path.read_text()) or {}
    equity_rupees = raw.get("equity_rupees")
    return RiskConfig(
        equity=None if equity_rupees is None else Money.from_rupees(Decimal(str(equity_rupees))),
        max_symbol_exposure_fraction=float(raw.get("max_symbol_exposure_fraction", 0.05)),
        max_portfolio_heat_fraction=float(raw.get("max_portfolio_heat_fraction", 0.025)),
        daily_loss_limit_fraction=float(raw.get("daily_loss_limit_fraction", 0.01)),
        max_trades_per_day=int(raw.get("max_trades_per_day", 3)),
        verified=bool(raw.get("verified", False)),
        source=str(raw.get("source", "")),
    )
