"""How large a position would be. Deliberately the most boring code here.

**There is no confidence parameter in this signature, and that is the whole
point** (FR-6.3, FR-7.8). Sizing on an agent's confidence is only defensible
once that confidence is shown to be calibrated, which needs 50 resolved ideas
beating a base-rate baseline (step 3.8). Until then risk per trade is flat.
The absence is enforced by a test that introspects the signature, so adding
the parameter fails CI rather than review.

**Flat 0.25% of equity**, capped by the structure's own max loss. For a
defined-risk spread the max loss per lot is known exactly, so the lot count is
a division - no volatility estimate, no Kelly fraction, nothing to be clever
about. Clever sizing is how accounts die.

Everything is integer paise (rule 4). Lots round **down**, always: rounding up
through a lot boundary silently exceeds the risk budget, and that is the one
error this module must never make.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal

from trading_desk.domain.values import Money

# Risk per trade, as a fraction of equity. FR-6.3: flat until calibration
# earns otherwise. Changing this is a decision with a DEVLOG entry.
DEFAULT_RISK_FRACTION = Decimal("0.0025")


@dataclass(frozen=True)
class PositionSize:
    """How many lots, and every number behind the answer (rule 9)."""

    lots: int
    risk_budget: Money
    max_loss_per_lot: Money
    total_risk: Money
    reason: str

    @property
    def is_tradable(self) -> bool:
        return self.lots > 0


def position_size(
    equity: Money,
    max_loss_per_lot: Money,
    lot_size: int,
    risk_fraction: Decimal = DEFAULT_RISK_FRACTION,
) -> PositionSize:
    """Lots to trade, from equity and the structure's own max loss.

    No confidence argument. See the module docstring - the absence is the
    feature, and a test asserts it by introspection.
    """
    if lot_size <= 0:
        raise ValueError(f"lot_size must be positive, got {lot_size}")
    if risk_fraction <= 0:
        raise ValueError(f"risk_fraction must be positive, got {risk_fraction}")

    budget = Money(paise=int((Decimal(equity.paise) * risk_fraction).to_integral_value(ROUND_DOWN)))

    loss_per_lot = Money(paise=max_loss_per_lot.paise * lot_size)
    if loss_per_lot.paise <= 0:
        return PositionSize(
            lots=0,
            risk_budget=budget,
            max_loss_per_lot=loss_per_lot,
            total_risk=Money.zero(),
            reason="max loss per lot is zero or negative - the structure is not priced",
        )

    # ROUND_DOWN, not nearest. One lot over the budget is a breach, and a
    # breach that rounds into existence is the worst kind.
    lots = budget.paise // loss_per_lot.paise
    total = Money(paise=lots * loss_per_lot.paise)

    if lots == 0:
        return PositionSize(
            lots=0,
            risk_budget=budget,
            max_loss_per_lot=loss_per_lot,
            total_risk=Money.zero(),
            reason=(
                f"one lot risks ₹{loss_per_lot.rupees():.2f}, above the "
                f"₹{budget.rupees():.2f} budget"
            ),
        )

    return PositionSize(
        lots=int(lots),
        risk_budget=budget,
        max_loss_per_lot=loss_per_lot,
        total_risk=total,
        reason=f"{lots} lot(s) risking ₹{total.rupees():.2f} of a ₹{budget.rupees():.2f} budget",
    )
