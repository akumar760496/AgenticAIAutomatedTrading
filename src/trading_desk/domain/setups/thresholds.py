"""Loading `config/setups.yaml`. Shapes the numbers, judges nothing.

Separate from the gate logic so the gates stay pure functions over plain
values - a gate that reads a file cannot be tested at a boundary without a
fixture on disk, and the boundary tests are the point of step 2.3.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

from trading_desk.domain.scoring import CreditExitRules, DebitExitRules

DEFAULT_SETUPS_PATH = Path("config/setups.yaml")


@dataclass(frozen=True)
class LiquidityLimits:
    min_open_interest_lots: int
    max_spread_fraction: float


@dataclass(frozen=True)
class ExpiryWindow:
    min_days: int
    max_days: int


@dataclass(frozen=True)
class DeltaBand:
    minimum: float
    maximum: float

    def contains(self, delta: float) -> bool:
        return self.minimum <= abs(delta) <= self.maximum


@dataclass(frozen=True)
class SetupAThresholds:
    max_sessions_since_catalyst: int
    catalyst_types: frozenset[str]
    min_credibility: float
    max_move_in_atr: float
    min_volume_ratio: float
    max_iv_rank: float
    liquidity: LiquidityLimits
    expiry: ExpiryWindow
    long_leg_delta: DeltaBand
    short_leg_delta: DeltaBand
    # How the idea ends. Added in step 3.8: the numbers were in docs/06 and in
    # no config file, which is how a rulebook number quietly becomes a literal.
    exits: DebitExitRules


@dataclass(frozen=True)
class SetupBThresholds:
    credit_regime_min_iv_rank: float
    debit_regime_max_iv_rank: float
    credit_delta: DeltaBand
    credit_expiry: ExpiryWindow
    debit_expiry: ExpiryWindow
    credit_exits: CreditExitRules
    debit_exits: DebitExitRules
    flat_by_sessions_before_expiry: int
    max_concurrent_index_positions: int
    blocking_event_types: frozenset[str]


@dataclass(frozen=True)
class SetupThresholds:
    setup_a: SetupAThresholds
    setup_b: SetupBThresholds
    allow_naked_shorts: bool
    verified: bool
    source: str


def load_setup_thresholds(path: Path = DEFAULT_SETUPS_PATH) -> SetupThresholds:
    if not path.is_file():
        raise FileNotFoundError(f"Setup thresholds not found at {path}")

    raw: dict[str, Any] = yaml.safe_load(path.read_text()) or {}
    a = raw["setup_a"]
    b = raw["setup_b"]

    return SetupThresholds(
        setup_a=SetupAThresholds(
            max_sessions_since_catalyst=int(a["max_sessions_since_catalyst"]),
            catalyst_types=frozenset(str(item).upper() for item in a["catalyst_types"]),
            min_credibility=float(a["min_credibility"]),
            max_move_in_atr=float(a["max_move_in_atr"]),
            min_volume_ratio=float(a["min_volume_ratio"]),
            max_iv_rank=float(a["max_iv_rank"]),
            liquidity=LiquidityLimits(
                min_open_interest_lots=int(a["liquidity"]["min_open_interest_lots"]),
                max_spread_fraction=float(a["liquidity"]["max_spread_fraction"]),
            ),
            expiry=ExpiryWindow(
                min_days=int(a["calendar"]["min_days_to_expiry"]),
                max_days=int(a["calendar"]["max_days_to_expiry"]),
            ),
            long_leg_delta=DeltaBand(
                float(a["structure"]["long_leg_delta_min"]),
                float(a["structure"]["long_leg_delta_max"]),
            ),
            short_leg_delta=DeltaBand(
                float(a["structure"]["short_leg_delta_min"]),
                float(a["structure"]["short_leg_delta_max"]),
            ),
            exits=_debit_exits(a["exits"]),
        ),
        setup_b=SetupBThresholds(
            credit_regime_min_iv_rank=float(b["credit_regime_min_iv_rank"]),
            debit_regime_max_iv_rank=float(b["debit_regime_max_iv_rank"]),
            credit_delta=DeltaBand(
                float(b["credit"]["short_leg_delta_min"]),
                float(b["credit"]["short_leg_delta_max"]),
            ),
            credit_expiry=ExpiryWindow(
                int(b["credit"]["min_days_to_expiry"]), int(b["credit"]["max_days_to_expiry"])
            ),
            debit_expiry=ExpiryWindow(
                int(b["debit"]["min_days_to_expiry"]), int(b["debit"]["max_days_to_expiry"])
            ),
            credit_exits=CreditExitRules(
                target_fraction_of_credit_captured=_fraction(
                    b["credit_exits"]["target_fraction_of_credit_captured"]
                ),
                stop_multiple_of_credit=_fraction(b["credit_exits"]["stop_multiple_of_credit"]),
            ),
            debit_exits=_debit_exits(b["debit_exits"]),
            flat_by_sessions_before_expiry=int(b["flat_by_sessions_before_expiry"]),
            max_concurrent_index_positions=int(b["max_concurrent_index_positions"]),
            blocking_event_types=frozenset(str(item).upper() for item in b["blocking_event_types"]),
        ),
        allow_naked_shorts=bool(raw.get("allow_naked_shorts", False)),
        verified=bool(raw.get("verified", False)),
        source=str(raw.get("source", "")),
    )


def _fraction(value: Any) -> Decimal:
    """A YAML number as a Decimal, via str.

    `Decimal(0.6)` is 0.59999999999999997779553950749686919152736663818359375.
    `Decimal(str(0.6))` is 0.6. Costs and P&L are computed from these, so the
    route through `str` is not pedantry (rule 4).
    """
    return Decimal(str(value))


def _debit_exits(raw: dict[str, Any]) -> DebitExitRules:
    """Shared by Setup A and Setup B's debit variant - the same three numbers."""
    return DebitExitRules(
        target_fraction_of_width=_fraction(raw["target_fraction_of_width"]),
        stop_fraction_of_debit_lost=_fraction(raw["stop_fraction_of_debit_lost"]),
        # Required, not defaulted. A missing time stop defaulting to 0 would
        # mean "flat on expiry day", which is the one thing docs/06 forbids.
        time_stop_days_to_expiry=int(raw["time_stop_days_to_expiry"]),
    )
