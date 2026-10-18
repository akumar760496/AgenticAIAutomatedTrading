"""Verify step 2.1 - domain models for research.

`docs/10`: done when "a single-source thesis raises; adding a `quantity` field
to `TradeProposal` fails the introspection test". Both are checked here, and
the second is checked **by actually adding the field** to a synthetic model
rather than by trusting that the test would have noticed.

The introspection walk is the point of the step. CLAUDE.md rule 1 says an LLM
never sizes an order, and FR-4.7 says a proposal must be structurally
incapable of carrying one. A prompt can be drifted away from; a constructor
that raises cannot.
"""

from __future__ import annotations

import dataclasses
import typing
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

from scripts.verify._runner import CheckResult
from trading_desk.domain.enums import Direction, Horizon, OptionRight, SetupId, SourceTier
from trading_desk.domain.proposals import (
    MAX_PROPOSALS_PER_CYCLE,
    EntryCondition,
    LegAction,
    ProposedLeg,
    ResearchCycleResult,
    StopBasis,
    StopMethod,
    TargetBasis,
    TargetMethod,
    TradeProposal,
)
from trading_desk.domain.research import MIN_INDEPENDENT_SOURCES, Evidence, Thesis
from trading_desk.domain.values import Price

TITLE = "Research domain models"

NOW = datetime(2026, 10, 19, 9, 0, tzinfo=UTC)

FORBIDDEN_SUBSTRINGS = (
    "quantity",
    "qty",
    "size",
    "lot",
    "notional",
    "capital",
    "margin",
    "order_type",
    "ordertype",
    "limit",
    "price",
    "amount",
    "units",
    "contracts",
)

# Types the walk must reach. If it silently found only TradeProposal, the
# no-size check would pass while inspecting almost nothing.
MUST_REACH = {"TradeProposal", "ProposedLeg", "StopBasis", "TargetBasis", "Thesis", "Evidence"}


def _evidence(source: str) -> Evidence:
    return Evidence(
        claim=f"{source} reported a demerger",
        source_name=source,
        source_ref=f"https://{source.lower()}.test/x",
        source_tier=SourceTier.EXCHANGE,
        observed_at=NOW,
        direction=Direction.BULLISH,
        strength=0.6,
    )


def _thesis(sources: tuple[str, ...]) -> Thesis:
    return Thesis(
        instrument_symbol="RELIANCE",
        claim="Demerger unlocks value",
        direction=Direction.BULLISH,
        horizon=Horizon.SWING_2W,
        evidence=tuple(_evidence(name) for name in sources),
        invalidation="close below 1380",
        confidence=0.62,
        created_at=NOW,
    )


def _proposal(rank: int = 1) -> TradeProposal:
    return TradeProposal(
        proposal_id=uuid4(),
        rank=rank,  # type: ignore[arg-type]
        instrument_symbol="RELIANCE",
        setup_id=SetupId.CATALYST_CONTINUATION,
        direction=Direction.BULLISH,
        horizon=Horizon.SWING_2W,
        thesis=_thesis(("NSE", "SEBI")),
        legs=(
            ProposedLeg(
                right=OptionRight.CALL,
                action=LegAction.BUY,
                strike=Price.from_rupees(Decimal("1400")),
                expiry=date(2026, 11, 26),
                delta=0.52,
            ),
        ),
        entry=EntryCondition(description="on a close above 1410"),
        stop=StopBasis(
            method=StopMethod.ATR_MULTIPLE,
            level=Price.from_rupees(Decimal("1380")),
            multiple=Decimal("1.5"),
        ),
        target=TargetBasis(
            method=TargetMethod.RANGE_PROJECTION, level=Price.from_rupees(Decimal("1460"))
        ),
        created_at=NOW,
    )


def _reachable(root: type) -> dict[str, type]:
    found: dict[str, type] = {}
    pending = [root]
    while pending:
        current = pending.pop()
        if not dataclasses.is_dataclass(current) or current.__name__ in found:
            continue
        found[current.__name__] = current
        for annotation in typing.get_type_hints(current).values():
            for candidate in (annotation, *typing.get_args(annotation)):
                if isinstance(candidate, type) and dataclasses.is_dataclass(candidate):
                    pending.append(candidate)
    return found


def _offending_fields(models: dict[str, type]) -> list[str]:
    offenders = []
    for name, model in models.items():
        for item in dataclasses.fields(model):
            lowered = item.name.lower()
            hits = [banned for banned in FORBIDDEN_SUBSTRINGS if banned in lowered]
            if hits:
                offenders.append(f"{name}.{item.name} {hits}")
    return offenders


def _the_walk_reaches_every_nested_type() -> tuple[bool, str]:
    reached = set(_reachable(TradeProposal))
    missing = MUST_REACH - reached
    return not missing, (
        f"{len(reached)} types reached: {sorted(reached)}"
        if not missing
        else f"walk missed {sorted(missing)}"
    )


def _no_field_can_carry_a_size_or_an_order() -> tuple[bool, str]:
    models = _reachable(TradeProposal)
    offenders = _offending_fields(models)
    total = sum(len(dataclasses.fields(model)) for model in models.values())
    return not offenders, (
        f"{total} fields across {len(models)} types, none matching {len(FORBIDDEN_SUBSTRINGS)} "
        "banned terms"
        if not offenders
        else f"OFFENDING: {offenders}"
    )


def _the_ban_list_is_not_vacuous() -> tuple[bool, str]:
    """Induced here, not assumed: a model carrying a quantity must be caught.

    Without this, a typo in FORBIDDEN_SUBSTRINGS would make the check above
    pass for the wrong reason, forever.
    """

    @dataclasses.dataclass(frozen=True)
    class WouldBeAnOrder:
        quantity: int
        lot_size: int
        limit_price: int

    caught = _offending_fields({"WouldBeAnOrder": WouldBeAnOrder})
    return len(caught) == 3, f"synthetic order-like model flagged {len(caught)}/3 fields"  # noqa: PLR2004


def _a_single_source_thesis_raises() -> tuple[bool, str]:
    try:
        _thesis(("NSE",))
    except ValueError as error:
        return "independent sources" in str(error), str(error)[:90]
    return False, "a single-source thesis was accepted"


def _two_items_from_one_source_are_one_source() -> tuple[bool, str]:
    """The case a naive count of evidence items would let through."""
    try:
        _thesis(("NSE", "nse"))
    except ValueError as error:
        return "independent sources" in str(error), f"refused: {str(error)[:70]}"
    return False, "two items from one publisher counted as two sources"


def _a_third_proposal_is_refused() -> tuple[bool, str]:
    try:
        ResearchCycleResult(
            cycle_id=uuid4(),
            generated_at=NOW,
            proposals=(_proposal(1), _proposal(2), _proposal(1)),
        )
    except ValueError as error:
        return "at most" in str(error), str(error)[:90]
    return False, f"more than {MAX_PROPOSALS_PER_CYCLE} proposals were accepted"


def _no_ideas_requires_a_reason() -> tuple[bool, str]:
    try:
        ResearchCycleResult(cycle_id=uuid4(), generated_at=NOW)
    except ValueError as error:
        return "must say why" in str(error), "an empty cycle must state a reason"
    return False, "an empty result with no reason was accepted"


def _everything_is_frozen_and_tz_aware() -> tuple[bool, str]:
    models = _reachable(TradeProposal)
    mutable = [
        name
        for name, model in models.items()
        if not getattr(model, "__dataclass_params__").frozen  # noqa: B009
    ]
    proposal = _proposal()
    aware = proposal.created_at.tzinfo is not None and proposal.thesis.created_at.tzinfo is not None
    return not mutable and aware, (
        f"{len(models)} types all frozen, timestamps tz-aware"
        if not mutable
        else f"mutable: {mutable}"
    )


def run() -> list[CheckResult]:
    walk_ok, walk_detail = _the_walk_reaches_every_nested_type()
    size_ok, size_detail = _no_field_can_carry_a_size_or_an_order()
    ban_ok, ban_detail = _the_ban_list_is_not_vacuous()
    single_ok, single_detail = _a_single_source_thesis_raises()
    dupe_ok, dupe_detail = _two_items_from_one_source_are_one_source()
    third_ok, third_detail = _a_third_proposal_is_refused()
    reason_ok, reason_detail = _no_ideas_requires_a_reason()
    frozen_ok, frozen_detail = _everything_is_frozen_and_tz_aware()

    return [
        CheckResult(
            name="the_introspection_walk_reaches_nested_types",
            passed=walk_ok,
            measured=walk_detail,
            requirement=f"need every type in {sorted(MUST_REACH)} reached",
        ),
        CheckResult(
            name="no_field_can_carry_a_size_or_an_order",
            passed=size_ok,
            measured=size_detail,
            requirement="need 0 fields matching a size, lot, order-type or order-price term",
        ),
        CheckResult(
            name="the_ban_list_actually_catches_an_order_field",
            passed=ban_ok,
            measured=ban_detail,
            requirement="need 3/3 - otherwise the check above passes for the wrong reason",
        ),
        CheckResult(
            name="a_single_source_thesis_raises",
            passed=single_ok,
            measured=single_detail,
            requirement=f"need {MIN_INDEPENDENT_SOURCES} independent sources, enforced by type",
        ),
        CheckResult(
            name="two_items_from_one_publisher_are_one_source",
            passed=dupe_ok,
            measured=dupe_detail,
            requirement="need independence counted by source, not by evidence item",
        ),
        CheckResult(
            name="a_third_proposal_in_one_cycle_is_refused",
            passed=third_ok,
            measured=third_detail,
            requirement=f"need at most {MAX_PROPOSALS_PER_CYCLE} proposals per cycle (FR-4.8)",
        ),
        CheckResult(
            name="a_cycle_with_no_ideas_must_state_why",
            passed=reason_ok,
            measured=reason_detail,
            requirement="need a reason - an empty list alone looks identical to a crash",
        ),
        CheckResult(
            name="every_model_is_frozen_with_tz_aware_timestamps",
            passed=frozen_ok,
            measured=frozen_detail,
            requirement="need all frozen (rule: no mutation) and tz-aware (rule 5)",
        ),
    ]
