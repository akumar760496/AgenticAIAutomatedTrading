"""What a trade actually costs. Integer paise throughout.

**Every rate here is an assumption until a real contract note confirms it**
(FR-10.3). They come from the published schedules as of October 2026 and are
in `config/costs.yaml` with `verified: false`, because statutory rates change
by circular and brokerage varies by plan. `verify 2_8` fails on that flag
until you reconcile against one of your own contract notes.

The ordering matters and is easy to get wrong:

- **STT on options is charged on the premium for a sale, and on the
  *settlement value* for an exercised long.** v1 never holds to expiry
  (Setup B is flat the session before, Setup A has a 5-day time stop), so the
  exercise case is not modelled - and `exercise_stt_not_modelled` says so
  rather than quietly returning a number that is too low.
- **GST applies to brokerage plus the exchange transaction charge**, not to
  the statutory taxes. Applying it to everything overstates the cost; applying
  it to brokerage alone understates it.
- **Slippage is not a cost in the regulatory sense but it is a cost.** It is
  modelled separately so it can be tuned against real fills without touching
  the statutory arithmetic.

Costs are computed **per leg**, because STT is charged on sells only and a
vertical has one of each. Computing on the net would be wrong in the
direction that flatters the strategy.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from trading_desk.domain.proposals import LegAction
from trading_desk.domain.values import Money


@dataclass(frozen=True)
class CostRates:
    """Statutory and broker rates. Loaded from config, never hardcoded."""

    brokerage_per_order: Money
    stt_sell_fraction: Decimal
    exchange_txn_fraction: Decimal
    sebi_fraction: Decimal
    stamp_duty_buy_fraction: Decimal
    gst_fraction: Decimal
    slippage_fraction: Decimal


@dataclass(frozen=True)
class LegCost:
    """One leg's costs, itemised so a contract note can be matched line by line."""

    action: LegAction
    turnover: Money
    brokerage: Money
    stt: Money
    exchange_txn: Money
    sebi: Money
    stamp_duty: Money
    gst: Money
    slippage: Money

    @property
    def total(self) -> Money:
        return Money(
            paise=(
                self.brokerage.paise
                + self.stt.paise
                + self.exchange_txn.paise
                + self.sebi.paise
                + self.stamp_duty.paise
                + self.gst.paise
                + self.slippage.paise
            )
        )


@dataclass(frozen=True)
class TradeCost:
    """Round-trip cost for every leg of a structure."""

    legs: tuple[LegCost, ...]

    @property
    def total(self) -> Money:
        return Money(paise=sum(leg.total.paise for leg in self.legs))

    def itemised(self) -> dict[str, Money]:
        """Totals by category, for matching against a contract note."""
        return {
            "brokerage": Money(paise=sum(leg.brokerage.paise for leg in self.legs)),
            "stt": Money(paise=sum(leg.stt.paise for leg in self.legs)),
            "exchange_txn": Money(paise=sum(leg.exchange_txn.paise for leg in self.legs)),
            "sebi": Money(paise=sum(leg.sebi.paise for leg in self.legs)),
            "stamp_duty": Money(paise=sum(leg.stamp_duty.paise for leg in self.legs)),
            "gst": Money(paise=sum(leg.gst.paise for leg in self.legs)),
            "slippage": Money(paise=sum(leg.slippage.paise for leg in self.legs)),
        }


def _apply(amount: Money, fraction: Decimal) -> Money:
    """A fraction of an amount, rounded half up once at the end (rule 4)."""
    return Money(paise=int((Decimal(amount.paise) * fraction).to_integral_value(ROUND_HALF_UP)))


def leg_cost(premium: Money, quantity: int, action: LegAction, rates: CostRates) -> LegCost:
    """Costs for one option leg.

    `quantity` is lots x lot size - the contract count. It is an argument, not
    a field on anything: this module computes costs for a hypothetical size,
    it does not decide one.
    """
    if quantity <= 0:
        raise ValueError(f"quantity must be positive, got {quantity}")

    turnover = Money(paise=premium.paise * quantity)

    # STT on the sell side only. This is the single biggest asymmetry between
    # a debit and a credit structure's costs.
    stt = _apply(turnover, rates.stt_sell_fraction) if action is LegAction.SELL else Money.zero()
    # Stamp duty on the buy side only.
    stamp = (
        _apply(turnover, rates.stamp_duty_buy_fraction) if action is LegAction.BUY else Money.zero()
    )

    brokerage = rates.brokerage_per_order
    exchange = _apply(turnover, rates.exchange_txn_fraction)
    sebi = _apply(turnover, rates.sebi_fraction)

    # GST on brokerage and the exchange charge only - not on statutory taxes.
    gst = _apply(Money(paise=brokerage.paise + exchange.paise), rates.gst_fraction)

    return LegCost(
        action=action,
        turnover=turnover,
        brokerage=brokerage,
        stt=stt,
        exchange_txn=exchange,
        sebi=sebi,
        stamp_duty=stamp,
        gst=gst,
        slippage=_apply(turnover, rates.slippage_fraction),
    )


def round_trip_cost(
    legs: tuple[tuple[Money, LegAction], ...], quantity: int, rates: CostRates
) -> TradeCost:
    """Entry and exit for every leg. A round trip is four orders for a vertical.

    Exit is modelled as the opposite action at the same premium. That is a
    simplification - the exit premium differs - but it keeps the *per-order*
    charges right, which are the ones that do not scale with price, and the
    turnover-linked ones are recomputed at exit by the caller when a real exit
    price is known.
    """
    costs = []
    for premium, action in legs:
        costs.append(leg_cost(premium, quantity, action, rates))
        opposite = LegAction.SELL if action is LegAction.BUY else LegAction.BUY
        costs.append(leg_cost(premium, quantity, opposite, rates))
    return TradeCost(legs=tuple(costs))


def exercise_stt_not_modelled() -> str:
    """Why the expiry case is absent, stated rather than silently omitted.

    STT on an exercised long option is charged on the *settlement value*, not
    the premium - a far larger base, and the classic way a backtest flatters
    itself. v1 never holds to expiry: Setup B is flat the session before, and
    Setup A carries a five-day time stop. If that ever changes, this is the
    first thing that must change with it.
    """
    return (
        "STT on exercise is charged on settlement value, not premium, and is not "
        "modelled because v1 never holds to expiry"
    )


@dataclass(frozen=True)
class CostConfig:
    """Rates plus the provenance that says whether to trust them."""

    rates: CostRates
    verified: bool
    source: str
