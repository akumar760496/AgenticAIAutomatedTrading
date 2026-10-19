"""The Options Strategist: which expiry, which structure, and at what cost.

`docs/02` asks for the volatility matrix to be **encoded**, so the choice of
debit against credit lives in `domain/options/selection.py` and this agent only
supplies it with measured inputs. The agent's own job is the plumbing either
side of that call: pick an expiry inside the setup's window, fetch the chain,
measure its liquidity, build the structure, and read the per-leg quotes back
out so Setup A's liquidity gate has something to compare.

**Nothing here can produce a naked short.** Both answers are verticals, and
`domain/options/structure.py` raises rather than return a single leg when no
protective wing is available (FR-5.9). A raise becomes a veto with the reason
attached - never a degraded structure.

Three numbers this agent needs are not in `config/setups.yaml`, and rather than
invent them it reports them as missing:

1. **Setup B's debit variant has no delta bands.** The config gives the credit
   variant a short-leg band and both variants an expiry window, but nothing
   says which strikes a Setup B debit spread buys. So that path refuses.
2. **No wing offset for a credit vertical.** Supplied by the caller, and the
   value needs sign-off; it decides the max loss.
3. **IV rank arrives as an argument.** It is computed by
   `application.iv_rank`, which reads two ports, and an agent may not import
   `application` (import-linter). The orchestrator computes it and hands it
   over - which also means the agent cannot quietly invent one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time

from trading_desk.domain.enums import Direction, OptionRight, SetupId
from trading_desk.domain.instruments import InstrumentRef
from trading_desk.domain.liquidity import LiquidityMeasure, measure_chain
from trading_desk.domain.market import OptionChainSnapshot, OptionQuote
from trading_desk.domain.options.selection import StructureChoice, StructureKind, choose_structure
from trading_desk.domain.options.structure import (
    StructureError,
    VerticalSpread,
    build_credit_vertical,
    build_debit_vertical,
)
from trading_desk.domain.setups.setup_a import LegLiquidity
from trading_desk.domain.setups.thresholds import ExpiryWindow, SetupThresholds
from trading_desk.domain.specialists import SpecialistFinding, SpecialistName
from trading_desk.domain.values import Money
from trading_desk.domain.vol import IvRank
from trading_desk.ports.option_chain import OptionChainPort

SETUP_B_DEBIT_BANDS_MISSING = (
    "Setup B debit delta bands: config/setups.yaml defines setup_b.credit "
    "short-leg deltas and both expiry windows, but no strikes for the debit "
    "variant - so a Setup B debit spread cannot be built"
)


@dataclass(frozen=True)
class OptionsView:
    """The structure, if one fits, and every number that chose it."""

    finding: SpecialistFinding
    choice: StructureChoice
    spread: VerticalSpread | None
    liquidity: LiquidityMeasure | None
    expiry: date | None
    days_to_expiry: int | None
    legs: tuple[LegLiquidity, ...]

    @property
    def has_structure(self) -> bool:
        return self.spread is not None


class OptionsAgent:
    def __init__(
        self,
        chains: OptionChainPort,
        thresholds: SetupThresholds,
        wing_offset: Money,
    ) -> None:
        self._chains = chains
        self._thresholds = thresholds
        self._wing_offset = wing_offset

    def propose(
        self,
        *,
        instrument: InstrumentRef,
        setup: SetupId,
        direction: Direction,
        iv_rank: IvRank,
        as_of: date,
        time_of_day: time | None = None,
        is_expiry_day: bool = False,
    ) -> OptionsView:
        window = self._expiry_window(setup, iv_rank)
        expiry = self._pick_expiry(instrument, window, as_of)
        if expiry is None:
            return self._refused(
                instrument.symbol,
                iv_rank,
                f"no expiry between {window.min_days} and {window.max_days} days out",
            )

        snapshot = self._chains.fetch_chain(instrument, expiry)
        days_out = (expiry - as_of).days
        liquidity = measure_chain(snapshot, is_index=_is_index(setup))

        choice = choose_structure(
            iv_rank=iv_rank.value,
            direction=direction,
            credit_above=self._credit_above(setup),
            debit_below=self._debit_below(setup),
            is_expiry_day=is_expiry_day,
            time_of_day=time_of_day,
        )
        if not choice.is_tradable:
            return self._refused(
                instrument.symbol,
                iv_rank,
                choice.reason,
                choice=choice,
                liquidity=liquidity,
                expiry=expiry,
                days_to_expiry=days_out,
            )

        spread, failure = self._build(snapshot, choice.kind, direction, setup)
        if spread is None:
            return self._refused(
                instrument.symbol,
                iv_rank,
                failure or "structure could not be built",
                choice=choice,
                liquidity=liquidity,
                expiry=expiry,
                days_to_expiry=days_out,
            )

        legs = _leg_liquidity(snapshot, spread)
        return OptionsView(
            finding=SpecialistFinding(
                specialist=SpecialistName.OPTIONS,
                symbol=instrument.symbol,
                measurements=_measurements(
                    choice=choice,
                    spread=spread,
                    liquidity=liquidity,
                    days_out=days_out,
                    iv_rank=iv_rank,
                    legs=legs,
                ),
                vetoes=(),
                unavailable=_iv_caveat(iv_rank),
                notes=(choice.reason,),
            ),
            choice=choice,
            spread=spread,
            liquidity=liquidity,
            expiry=expiry,
            days_to_expiry=days_out,
            legs=legs,
        )

    def _build(
        self,
        snapshot: OptionChainSnapshot,
        kind: StructureKind,
        direction: Direction,
        setup: SetupId,
    ) -> tuple[VerticalSpread | None, str | None]:
        """Build the chosen vertical, or say why it could not be built."""
        if kind is StructureKind.CREDIT_VERTICAL:
            band = self._thresholds.setup_b.credit_delta
            try:
                return build_credit_vertical(snapshot, direction, band, self._wing_offset), None
            except StructureError as error:
                return None, str(error)

        if setup is SetupId.INDEX_VOL_REGIME:
            return None, SETUP_B_DEBIT_BANDS_MISSING

        limits = self._thresholds.setup_a
        try:
            spread = build_debit_vertical(
                snapshot, direction, limits.long_leg_delta, limits.short_leg_delta
            )
        except StructureError as error:
            return None, str(error)
        return spread, None

    def _expiry_window(self, setup: SetupId, iv_rank: IvRank) -> ExpiryWindow:
        if setup is SetupId.CATALYST_CONTINUATION:
            return self._thresholds.setup_a.expiry
        # Setup B's window depends on which side of the regime we are on, and
        # an unknown IV rank is handled before the structure is built, so the
        # debit window is the harmless default here.
        value = iv_rank.value
        credit_above = self._thresholds.setup_b.credit_regime_min_iv_rank
        if value is not None and value >= credit_above:
            return self._thresholds.setup_b.credit_expiry
        return self._thresholds.setup_b.debit_expiry

    def _credit_above(self, setup: SetupId) -> float | None:
        """Setup A never sells premium, so it has no credit threshold at all."""
        if setup is SetupId.CATALYST_CONTINUATION:
            return None
        return self._thresholds.setup_b.credit_regime_min_iv_rank

    def _debit_below(self, setup: SetupId) -> float:
        if setup is SetupId.CATALYST_CONTINUATION:
            return self._thresholds.setup_a.max_iv_rank
        return self._thresholds.setup_b.debit_regime_max_iv_rank

    def _pick_expiry(
        self, instrument: InstrumentRef, window: ExpiryWindow, as_of: date
    ) -> date | None:
        """The nearest listed expiry inside the window, or None."""
        for expiry in sorted(self._chains.available_expiries(instrument)):
            days = (expiry - as_of).days
            if window.min_days <= days <= window.max_days:
                return expiry
        return None

    def _refused(
        self,
        symbol: str,
        iv_rank: IvRank,
        reason: str,
        *,
        choice: StructureChoice | None = None,
        liquidity: LiquidityMeasure | None = None,
        expiry: date | None = None,
        days_to_expiry: int | None = None,
    ) -> OptionsView:
        decided = choice or StructureChoice(
            kind=StructureKind.NONE, reason=reason, iv_rank=iv_rank.value
        )
        measurements = {"iv_rank": iv_rank.describe()}
        if days_to_expiry is not None:
            measurements["days_to_expiry"] = str(days_to_expiry)
        if liquidity is not None:
            measurements["chain_open_interest"] = str(liquidity.open_interest)
            measurements["legs_priced"] = f"{liquidity.priced_fraction:.0%}"

        return OptionsView(
            finding=SpecialistFinding(
                specialist=SpecialistName.OPTIONS,
                symbol=symbol,
                measurements=measurements,
                vetoes=(reason,),
                unavailable=_iv_caveat(iv_rank),
            ),
            choice=decided,
            spread=None,
            liquidity=liquidity,
            expiry=expiry,
            days_to_expiry=days_to_expiry,
            legs=(),
        )


def _is_index(setup: SetupId) -> bool:
    return setup is SetupId.INDEX_VOL_REGIME


def _iv_caveat(iv_rank: IvRank) -> tuple[str, ...]:
    if iv_rank.is_available:
        return ()
    return (f"iv_rank: {iv_rank.reason or 'unavailable'}",)


def _leg_liquidity(
    snapshot: OptionChainSnapshot, spread: VerticalSpread
) -> tuple[LegLiquidity, ...]:
    """Read each chosen leg's quote back out of the chain.

    Setup A's liquidity gate compares open interest and spread width per leg,
    and those numbers have to come from the same snapshot the structure was
    built from. Re-fetching would risk measuring a different chain.
    """
    legs: list[LegLiquidity] = []
    for leg in (spread.long_leg, spread.short_leg):
        quote = _quote_at(snapshot, leg.strike.paise, leg.right)
        if quote is None:
            continue
        legs.append(
            LegLiquidity(
                open_interest_lots=quote.open_interest,
                bid=Money(paise=quote.bid.paise),
                ask=Money(paise=quote.ask.paise),
            )
        )
    return tuple(legs)


def _quote_at(
    snapshot: OptionChainSnapshot, strike_paise: int, right: OptionRight
) -> OptionQuote | None:
    for strike in snapshot.strikes:
        if strike.strike.paise != strike_paise:
            continue
        return strike.call if right is OptionRight.CALL else strike.put
    return None


def _measurements(
    *,
    choice: StructureChoice,
    spread: VerticalSpread,
    liquidity: LiquidityMeasure,
    days_out: int,
    iv_rank: IvRank,
    legs: tuple[LegLiquidity, ...],
) -> dict[str, str]:
    spreads = [leg.spread_fraction() for leg in legs]
    widest = max((value for value in spreads if value is not None), default=None)
    return {
        "structure": choice.kind.value,
        "iv_rank": iv_rank.describe(),
        "days_to_expiry": str(days_out),
        "max_loss_per_unit": f"₹{spread.max_loss.rupees():,.2f}",
        "max_gain_per_unit": f"₹{spread.max_gain.rupees():,.2f}",
        "width": f"₹{spread.width.rupees():,.2f}",
        "breakeven": f"₹{spread.breakeven.rupees():,.2f}",
        "min_leg_open_interest": str(min((leg.open_interest_lots for leg in legs), default=0)),
        "widest_leg_spread": "unavailable" if widest is None else f"{widest:.2%}",
        "chain_open_interest": str(liquidity.open_interest),
        "legs_priced": f"{liquidity.priced_fraction:.0%}",
    }
