"""Turning "bullish on X, 15 DTE" into specific strikes.

**A naked short is not constructible here** (FR-5.9). `build_debit_vertical`
and `build_credit_vertical` are the only two builders, both return exactly two
legs, and the credit builder returns a structure only if it found a protective
wing. There is no function that emits a lone short leg, so the flag in
`config/setups.yaml` is a second lock on a door that has no handle.

Everything is in integer paise (rule 4). Max loss, max gain and breakeven are
computed from the leg prices, not estimated: these numbers reach the risk gate
and the briefing, and a rounded-off max loss is a wrong risk number.

**What this does not do: size the position.** It returns the structure and its
per-lot economics. Converting that into a lot count is the risk gate's job in
step 2.7, and nothing here has a quantity field.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from trading_desk.domain.enums import Direction, OptionRight
from trading_desk.domain.market import OptionChainSnapshot, OptionQuote, OptionStrike
from trading_desk.domain.proposals import LegAction, ProposedLeg
from trading_desk.domain.setups.thresholds import DeltaBand
from trading_desk.domain.values import Money, Price

# A leg quoted wider than this cannot be filled near mid, so the structure's
# computed economics would be fiction.
MAX_LEG_SPREAD_FRACTION = 0.10


class StructureError(ValueError):
    """The chain cannot support the requested structure."""


@dataclass(frozen=True)
class VerticalSpread:
    """A two-leg defined-risk structure and its per-lot economics.

    `max_loss` and `max_gain` are per lot and exclude costs. The cost model in
    step 2.8 applies brokerage, STT and the rest; keeping them separate means
    the structure's own arithmetic can be checked against a broker's payoff
    diagram.
    """

    long_leg: ProposedLeg
    short_leg: ProposedLeg
    # Positive for a debit paid, negative for a credit received.
    net_debit: Money
    max_loss: Money
    max_gain: Money
    breakeven: Price
    width: Money

    @property
    def is_credit(self) -> bool:
        return self.net_debit.paise < 0

    def describe(self) -> str:
        kind = "credit" if self.is_credit else "debit"
        return (
            f"{kind} vertical {self.long_leg.strike.rupees():g}/"
            f"{self.short_leg.strike.rupees():g} {self.long_leg.expiry}, "
            f"max loss ₹{self.max_loss.rupees():.2f}, max gain ₹{self.max_gain.rupees():.2f}"
        )


def _mid(quote: OptionQuote) -> Money | None:
    """Mid price, or None when the quote is too wide or absent to trust."""
    if quote.bid.paise <= 0 or quote.ask.paise <= 0 or quote.ask.paise < quote.bid.paise:
        return None
    mid = (quote.bid.paise + quote.ask.paise) // 2
    if mid <= 0:
        return None
    if (quote.ask.paise - quote.bid.paise) / mid > MAX_LEG_SPREAD_FRACTION:
        return None
    return Money(paise=mid)


def _side(strike: OptionStrike, right: OptionRight) -> OptionQuote | None:
    return strike.call if right is OptionRight.CALL else strike.put


def select_by_delta(
    snapshot: OptionChainSnapshot, right: OptionRight, band: DeltaBand
) -> OptionStrike | None:
    """The strike whose delta sits in `band`, closest to its middle.

    Closest-to-middle rather than first-match: chains are not evenly spaced,
    and taking the first strike inside a band biases every structure toward
    one edge of it.
    """
    target = (band.minimum + band.maximum) / 2
    candidates = []
    for strike in snapshot.strikes:
        quote = _side(strike, right)
        if quote is None or quote.delta is None or _mid(quote) is None:
            continue
        if band.contains(quote.delta):
            candidates.append((abs(abs(quote.delta) - target), strike))

    if not candidates:
        return None
    return min(candidates, key=lambda pair: pair[0])[1]


def _leg(strike: OptionStrike, right: OptionRight, action: LegAction, expiry: date) -> ProposedLeg:
    quote = _side(strike, right)
    return ProposedLeg(
        right=right,
        action=action,
        strike=strike.strike,
        expiry=expiry,
        delta=quote.delta if quote else None,
    )


def _require_mid(strike: OptionStrike, right: OptionRight, label: str) -> Money:
    quote = _side(strike, right)
    if quote is None:
        raise StructureError(f"{label} leg has no {right} quote")
    mid = _mid(quote)
    if mid is None:
        raise StructureError(
            f"{label} leg is unpriceable: bid {quote.bid.rupees()}, ask {quote.ask.rupees()} "
            f"(wider than {MAX_LEG_SPREAD_FRACTION:.0%} of mid)"
        )
    return mid


def build_debit_vertical(
    snapshot: OptionChainSnapshot,
    direction: Direction,
    long_band: DeltaBand,
    short_band: DeltaBand,
) -> VerticalSpread:
    """Long near the money, short further out. Max loss is the debit paid.

    Raises rather than returning None: a chain too thin to build in is a fact
    the caller must handle, and a None here would be easy to treat as "no
    structure needed".
    """
    right = OptionRight.CALL if direction is Direction.BULLISH else OptionRight.PUT
    if direction is Direction.NEUTRAL:
        raise StructureError("a debit vertical needs a direction")

    long_strike = select_by_delta(snapshot, right, long_band)
    short_strike = select_by_delta(snapshot, right, short_band)
    if long_strike is None or short_strike is None:
        raise StructureError(
            f"{snapshot.underlying.symbol}: no strikes in the delta bands "
            f"{long_band.minimum}-{long_band.maximum} / "
            f"{short_band.minimum}-{short_band.maximum}"
        )
    if long_strike.strike.paise == short_strike.strike.paise:
        raise StructureError(
            f"{snapshot.underlying.symbol}: both delta bands resolved to the same strike - "
            "the chain is too coarse for this structure"
        )

    long_mid = _require_mid(long_strike, right, "long")
    short_mid = _require_mid(short_strike, right, "short")

    net_debit = Money(paise=long_mid.paise - short_mid.paise)
    if net_debit.paise <= 0:
        raise StructureError(
            f"{snapshot.underlying.symbol}: the 'debit' spread prices as a credit "
            f"({net_debit.rupees()}), which means the quotes are inconsistent"
        )

    width = Money(paise=abs(short_strike.strike.paise - long_strike.strike.paise))
    expiry = date.fromisoformat(snapshot.expiry_date)

    # Breakeven moves away from the long strike by the debit, in the direction
    # the structure profits.
    offset = net_debit.paise if right is OptionRight.CALL else -net_debit.paise
    return VerticalSpread(
        long_leg=_leg(long_strike, right, LegAction.BUY, expiry),
        short_leg=_leg(short_strike, right, LegAction.SELL, expiry),
        net_debit=net_debit,
        max_loss=net_debit,
        max_gain=Money(paise=width.paise - net_debit.paise),
        breakeven=Price(paise=long_strike.strike.paise + offset),
        width=width,
    )


def build_credit_vertical(
    snapshot: OptionChainSnapshot,
    direction: Direction,
    short_band: DeltaBand,
    wing_offset: Money,
) -> VerticalSpread:
    """Short near the money, long further out as the protective wing.

    **The wing is not optional.** If no strike exists to buy, this raises
    rather than returning a bare short - which is the whole of FR-5.9 in one
    control-flow decision.
    """
    if direction is Direction.NEUTRAL:
        raise StructureError("a credit vertical needs a direction")
    right = OptionRight.PUT if direction is Direction.BULLISH else OptionRight.CALL

    short_strike = select_by_delta(snapshot, right, short_band)
    if short_strike is None:
        raise StructureError(
            f"{snapshot.underlying.symbol}: no strike in the delta band "
            f"{short_band.minimum}-{short_band.maximum}"
        )

    wanted = (
        short_strike.strike.paise - wing_offset.paise
        if right is OptionRight.PUT
        else short_strike.strike.paise + wing_offset.paise
    )
    # The wing must be *further out of the money* than the short leg: below it
    # for puts, above it for calls. Taking the nearest strike regardless of
    # side can pick one between the short and the money, which inverts the
    # spread - it prices as a debit and its "max loss" is nonsense. Found by a
    # fixture where the only other priced put sat above the short strike.
    wing = _nearest_priced_strike(
        snapshot,
        right,
        wanted,
        below=short_strike.strike.paise if right is OptionRight.PUT else None,
        above=short_strike.strike.paise if right is OptionRight.CALL else None,
    )
    if wing is None:
        raise StructureError(
            f"{snapshot.underlying.symbol}: no protective wing available near "
            f"{Price(paise=wanted).rupees()} - refusing to emit a naked short (FR-5.9)"
        )

    short_mid = _require_mid(short_strike, right, "short")
    wing_mid = _require_mid(wing, right, "wing")

    credit = Money(paise=short_mid.paise - wing_mid.paise)
    if credit.paise <= 0:
        raise StructureError(
            f"{snapshot.underlying.symbol}: the 'credit' spread prices as a debit - "
            "the quotes are inconsistent"
        )

    width = Money(paise=abs(short_strike.strike.paise - wing.strike.paise))
    expiry = date.fromisoformat(snapshot.expiry_date)
    offset = -credit.paise if right is OptionRight.PUT else credit.paise

    return VerticalSpread(
        long_leg=_leg(wing, right, LegAction.BUY, expiry),
        short_leg=_leg(short_strike, right, LegAction.SELL, expiry),
        net_debit=Money(paise=-credit.paise),
        max_loss=Money(paise=width.paise - credit.paise),
        max_gain=credit,
        breakeven=Price(paise=short_strike.strike.paise + offset),
        width=width,
    )


def _nearest_priced_strike(
    snapshot: OptionChainSnapshot,
    right: OptionRight,
    wanted_paise: int,
    below: int | None = None,
    above: int | None = None,
) -> OptionStrike | None:
    """The priced strike closest to `wanted_paise`, on the permitted side only."""
    candidates = []
    for strike in snapshot.strikes:
        if below is not None and strike.strike.paise >= below:
            continue
        if above is not None and strike.strike.paise <= above:
            continue
        quote = _side(strike, right)
        if quote is None or _mid(quote) is None:
            continue
        candidates.append((abs(strike.strike.paise - wanted_paise), strike))
    if not candidates:
        return None
    return min(candidates, key=lambda pair: pair[0])[1]


def max_loss_in_rupees(spread: VerticalSpread, lot_size: int) -> Decimal:
    """Per-lot max loss scaled by contract size, for the briefing to display.

    Takes `lot_size` as an argument rather than reading it from anywhere: the
    lot size is an exchange rule from config, and this stays a pure function.
    """
    if lot_size <= 0:
        raise ValueError(f"lot_size must be positive, got {lot_size}")
    return spread.max_loss.rupees() * lot_size
