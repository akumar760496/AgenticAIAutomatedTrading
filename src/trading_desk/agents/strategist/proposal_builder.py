"""Turning a ranked, synthesised candidate into a `TradeProposal`.

Separate from the graph because this is where the type system does its
arguing, and that deserves to be read on its own.

**Every level in the proposal comes from the structure, not from a multiplier.**
The stop is the long leg's strike and the target is the short leg's strike,
because for a vertical those are the two prices where the position's behaviour
changes. The obvious alternative - "stop at 1.5 ATR" - would need a 1.5 that
nothing in `config/` contains and nothing has backtested, and inventing one
here would put an unexamined number into every briefing.

**A thesis with one source raises, and the raise is the point.** `Thesis`
requires two independent sources (FR-4.6). An exchange filing nobody has
written about yet is one source, and on today's archive that is most of them -
so most candidates are expected to die here, visibly, in the rejected list,
rather than quietly become a one-source idea.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, cast
from uuid import NAMESPACE_URL, UUID, uuid5

from trading_desk.agents.strategist.candidates import SymbolInputs
from trading_desk.agents.strategist.synthesis import Synthesis
from trading_desk.domain.enums import Direction, Horizon, OptionRight
from trading_desk.domain.options.structure import VerticalSpread
from trading_desk.domain.proposals import (
    EntryCondition,
    LegAction,
    ProposedLeg,
    StopBasis,
    StopMethod,
    TargetBasis,
    TargetMethod,
    TradeProposal,
)
from trading_desk.domain.ranking import Candidate
from trading_desk.domain.research import Evidence, Thesis
from trading_desk.domain.values import Price

# docs/06 scopes v1 to short-horizon ideas. The impact analyst answers in days,
# and these are the two buckets a vertical with a 10-25 day expiry can be.
SWING_3D_MAX_DAYS = 3


def proposal_id_for(cycle_id: UUID, symbol: str) -> UUID:
    """Derived, not random, so the same cycle re-run produces the same id.

    A `uuid4` here would mean a resumed cycle wrote a second set of ideas that
    nothing could match against the first - and matching across a resume is
    exactly what FR-4.12 is for.
    """
    return uuid5(NAMESPACE_URL, f"trading-desk/{cycle_id}/{symbol}")


def horizon_for(days: int) -> Horizon:
    return Horizon.SWING_3D if days <= SWING_3D_MAX_DAYS else Horizon.SWING_2W


def build_thesis(
    inputs: SymbolInputs, candidate: Candidate, synthesis: Synthesis, created_at: datetime
) -> Thesis:
    """Raises `ValueError` when there are fewer than two independent sources."""
    catalyst = inputs.catalyst
    evidence = [
        Evidence(
            claim=catalyst.article.headline,
            source_name=catalyst.article.publisher,
            source_ref=catalyst.article.url,
            source_tier=catalyst.article.source_tier,
            observed_at=catalyst.article.published_at,
            direction=catalyst.impact.direction,
            strength=catalyst.credibility.value,
        )
    ]
    evidence.extend(
        Evidence(
            claim=f"{publisher} carried the same story",
            source_name=publisher,
            source_ref=catalyst.article.url,
            source_tier=catalyst.article.source_tier,
            observed_at=catalyst.article.published_at,
            direction=catalyst.impact.direction,
            strength=catalyst.credibility.value,
        )
        for publisher in catalyst.also_reported_by
    )

    return Thesis(
        instrument_symbol=candidate.symbol,
        claim=synthesis.claim,
        direction=candidate.direction,
        horizon=horizon_for(catalyst.impact.horizon_days),
        evidence=tuple(evidence),
        invalidation=synthesis.invalidation,
        confidence=candidate.confidence(),
        created_at=created_at,
    )


def legs_from(spread: VerticalSpread) -> tuple[ProposedLeg, ...]:
    """The two legs as the briefing states them. No quantity exists to copy."""
    return (
        ProposedLeg(
            right=spread.long_leg.right,
            action=LegAction.BUY,
            strike=spread.long_leg.strike,
            expiry=spread.long_leg.expiry,
            delta=spread.long_leg.delta,
        ),
        ProposedLeg(
            right=spread.short_leg.right,
            action=LegAction.SELL,
            strike=spread.short_leg.strike,
            expiry=spread.short_leg.expiry,
            delta=spread.short_leg.delta,
        ),
    )


def _stop_and_target(spread: VerticalSpread, direction: Direction) -> tuple[StopBasis, TargetBasis]:
    """Both levels read off the structure. See the module docstring."""
    side = "below" if direction is Direction.BULLISH else "above"
    protective = "put" if spread.long_leg.right is OptionRight.PUT else "call"
    stop = StopBasis(
        method=StopMethod.STRUCTURE,
        level=Price(paise=spread.long_leg.strike.paise),
        rationale=(
            f"{side} the long {protective} strike the spread is working against you; "
            f"the loss is capped at the {spread.max_loss} debit either way"
        ),
    )
    target = TargetBasis(
        method=TargetMethod.STRUCTURE,
        level=Price(paise=spread.short_leg.strike.paise),
        rationale="the spread reaches its maximum value at the short strike",
    )
    return stop, target


def build_proposal(
    *,
    cycle_id: UUID,
    rank: int,
    inputs: SymbolInputs,
    candidate: Candidate,
    thesis: Thesis,
    created_at: datetime,
) -> TradeProposal:
    spread = inputs.options.spread
    if spread is None:
        raise ValueError(f"{candidate.symbol}: no structure to propose")

    stop, target = _stop_and_target(spread, candidate.direction)
    return TradeProposal(
        proposal_id=proposal_id_for(cycle_id, candidate.symbol),
        # `rank` is Literal[1, 2] and ranking already capped the list at two;
        # TradeProposal re-checks it at runtime, which is where a bad value
        # would actually be caught.
        rank=cast("Literal[1, 2]", rank),
        instrument_symbol=candidate.symbol,
        setup_id=candidate.setup_id,
        direction=candidate.direction,
        horizon=thesis.horizon,
        thesis=thesis,
        legs=legs_from(spread),
        entry=EntryCondition(
            description="at the open, if both legs are still priced inside the liquidity gate"
        ),
        stop=stop,
        target=target,
        created_at=created_at,
        gate_values=dict(candidate.gate_values),
        used_iv_proxy=candidate.used_iv_proxy,
    )
