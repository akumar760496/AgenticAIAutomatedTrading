"""A proposal must be structurally incapable of becoming an order (FR-4.7).

The introspection test below is the one that matters. It walks every type
reachable from `TradeProposal` and fails if any field name looks like a size,
a lot count, an order type or an order price. The point is that a future
addition - made helpfully, in good faith, at 11pm - fails CI rather than
review.
"""

from __future__ import annotations

import dataclasses
import typing
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from trading_desk.domain.enums import Direction, Horizon, OptionRight, SetupId, SourceTier
from trading_desk.domain.proposals import (
    MAX_PROPOSALS_PER_CYCLE,
    EntryCondition,
    LegAction,
    ProposedLeg,
    RejectedCandidate,
    ResearchCycleResult,
    StopBasis,
    StopMethod,
    TargetBasis,
    TargetMethod,
    TradeProposal,
)
from trading_desk.domain.research import Evidence, Thesis
from trading_desk.domain.values import Price

NOW = datetime(2026, 10, 19, 9, 0, tzinfo=UTC)

# Anything that would let a proposal be executed rather than read. `price` is
# here deliberately: a *level* describes an idea, an order price instructs.
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


def _evidence(source: str, direction: Direction = Direction.BULLISH) -> Evidence:
    return Evidence(
        claim=f"{source} reported a demerger",
        source_name=source,
        source_ref=f"https://{source.lower()}.test/story",
        source_tier=SourceTier.TIER_1,
        observed_at=NOW,
        direction=direction,
        strength=0.6,
    )


def _thesis(symbol: str = "RELIANCE", sources: tuple[str, ...] = ("NSE", "SEBI")) -> Thesis:
    return Thesis(
        instrument_symbol=symbol,
        claim="Demerger unlocks value",
        direction=Direction.BULLISH,
        horizon=Horizon.SWING_2W,
        evidence=tuple(_evidence(source) for source in sources),
        invalidation="close below 1380",
        confidence=0.62,
        created_at=NOW,
    )


def _proposal(rank: int = 1, symbol: str = "RELIANCE") -> TradeProposal:
    return TradeProposal(
        proposal_id=uuid4(),
        rank=rank,  # type: ignore[arg-type]
        instrument_symbol=symbol,
        setup_id=SetupId.CATALYST_CONTINUATION,
        direction=Direction.BULLISH,
        horizon=Horizon.SWING_2W,
        thesis=_thesis(symbol),
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


def _reachable_dataclasses(root: type) -> dict[str, type]:
    """Every dataclass reachable from `root` through its field annotations.

    Walks the graph rather than checking `TradeProposal` alone: a quantity on
    `ProposedLeg` is the same loophole one level down, and that is exactly
    where it would be added.
    """
    found: dict[str, type] = {}
    pending = [root]
    while pending:
        current = pending.pop()
        if not dataclasses.is_dataclass(current) or current.__name__ in found:
            continue
        found[current.__name__] = current
        hints = typing.get_type_hints(current)
        for annotation in hints.values():
            for candidate in (annotation, *typing.get_args(annotation)):
                if isinstance(candidate, type) and dataclasses.is_dataclass(candidate):
                    pending.append(candidate)
    return found


class TestNoOrderFieldsAnywhere:
    def test_the_walk_actually_reaches_the_nested_types(self) -> None:
        """Guards the guard. If the walk silently found only TradeProposal,
        the real test below would pass while checking almost nothing."""
        reachable = _reachable_dataclasses(TradeProposal)

        assert {"TradeProposal", "ProposedLeg", "StopBasis", "TargetBasis", "Thesis"} <= set(
            reachable
        )

    def test_no_reachable_field_can_carry_a_size_or_an_order(self) -> None:
        """FR-4.7. An LLM never sizes a position; the type makes it impossible."""
        offenders = []
        for name, model in _reachable_dataclasses(TradeProposal).items():
            for dataclass_field in dataclasses.fields(model):
                lowered = dataclass_field.name.lower()
                for banned in FORBIDDEN_SUBSTRINGS:
                    if banned in lowered:
                        offenders.append(f"{name}.{dataclass_field.name} (matched {banned!r})")

        assert not offenders, (
            "A proposal must not be able to carry a size or an order instruction. "
            f"Offending fields: {offenders}"
        )

    def test_the_ban_list_would_actually_catch_something(self) -> None:
        """Induces the check: a model with a quantity must be flagged. Without
        this, a typo in FORBIDDEN_SUBSTRINGS makes the test above vacuous."""

        @dataclasses.dataclass(frozen=True)
        class Sneaky:
            quantity: int

        offenders = [
            item.name
            for item in dataclasses.fields(Sneaky)
            if any(banned in item.name.lower() for banned in FORBIDDEN_SUBSTRINGS)
        ]
        assert offenders == ["quantity"]


class TestThesisRequiresTwoIndependentSources:
    def test_a_single_source_thesis_raises(self) -> None:
        with pytest.raises(ValueError, match="independent sources"):
            _thesis(sources=("NSE",))

    def test_two_items_from_one_source_are_still_one_source(self) -> None:
        """The subtle case. Two quotes from the same publisher are not two
        sources, and counting evidence items rather than sources would let a
        single-source thesis through."""
        with pytest.raises(ValueError, match="independent sources"):
            Thesis(
                instrument_symbol="RELIANCE",
                claim="Demerger unlocks value",
                direction=Direction.BULLISH,
                horizon=Horizon.SWING_2W,
                evidence=(_evidence("NSE"), _evidence("NSE")),
                invalidation="close below 1380",
                confidence=0.6,
                created_at=NOW,
            )

    def test_source_names_are_compared_case_insensitively(self) -> None:
        with pytest.raises(ValueError, match="independent sources"):
            _thesis(sources=("NSE", "nse"))

    def test_two_genuine_sources_are_accepted(self) -> None:
        assert len(_thesis(sources=("NSE", "SEBI")).independent_sources()) == 2

    def test_a_thesis_must_say_what_would_make_it_wrong(self) -> None:
        with pytest.raises(ValueError, match="make it wrong"):
            Thesis(
                instrument_symbol="RELIANCE",
                claim="Demerger unlocks value",
                direction=Direction.BULLISH,
                horizon=Horizon.SWING_2W,
                evidence=(_evidence("NSE"), _evidence("SEBI")),
                invalidation="   ",
                confidence=0.6,
                created_at=NOW,
            )

    def test_contradicting_evidence_is_kept_not_dropped(self) -> None:
        thesis = Thesis(
            instrument_symbol="RELIANCE",
            claim="Demerger unlocks value",
            direction=Direction.BULLISH,
            horizon=Horizon.SWING_2W,
            evidence=(
                _evidence("NSE"),
                _evidence("SEBI"),
                _evidence("MINT", direction=Direction.BEARISH),
            ),
            invalidation="close below 1380",
            confidence=0.6,
            created_at=NOW,
        )

        assert len(thesis.supporting()) == 2
        assert len(thesis.contradicting()) == 1


class TestProposalConsistency:
    def test_a_proposal_whose_thesis_is_about_another_instrument_raises(self) -> None:
        with pytest.raises(ValueError, match="thesis is about"):
            TradeProposal(
                proposal_id=uuid4(),
                rank=1,
                instrument_symbol="RELIANCE",
                setup_id=SetupId.CATALYST_CONTINUATION,
                direction=Direction.BULLISH,
                horizon=Horizon.SWING_2W,
                thesis=_thesis("INFY"),
                legs=_proposal().legs,
                entry=EntryCondition(description="x"),
                stop=StopBasis(method=StopMethod.PERCENT, level=Price.from_rupees(Decimal("1380"))),
                target=TargetBasis(
                    method=TargetMethod.STRUCTURE, level=Price.from_rupees(Decimal("1460"))
                ),
                created_at=NOW,
            )

    def test_a_bullish_proposal_with_a_bearish_thesis_raises(self) -> None:
        with pytest.raises(ValueError, match="is BEARISH"):
            TradeProposal(
                proposal_id=uuid4(),
                rank=1,
                instrument_symbol="RELIANCE",
                setup_id=SetupId.CATALYST_CONTINUATION,
                direction=Direction.BEARISH,
                horizon=Horizon.SWING_2W,
                thesis=_thesis(),
                legs=_proposal().legs,
                entry=EntryCondition(description="x"),
                stop=StopBasis(method=StopMethod.PERCENT, level=Price.from_rupees(Decimal("1380"))),
                target=TargetBasis(
                    method=TargetMethod.STRUCTURE, level=Price.from_rupees(Decimal("1460"))
                ),
                created_at=NOW,
            )

    def test_rank_three_is_refused_at_runtime_not_only_by_mypy(self) -> None:
        """An LLM-built proposal arrives as data; mypy never sees it."""
        with pytest.raises(ValueError, match="rank must be 1 or 2"):
            _proposal(rank=3)

    def test_a_proposal_needs_a_leg(self) -> None:
        with pytest.raises(ValueError, match="at least one leg"):
            dataclasses.replace(_proposal(), legs=())


class TestCycleResult:
    def test_at_most_two_proposals(self) -> None:
        with pytest.raises(ValueError, match="at most 2"):
            ResearchCycleResult(
                cycle_id=uuid4(),
                generated_at=NOW,
                proposals=(_proposal(1), _proposal(2), _proposal(1, "INFY")),
            )

    def test_two_proposals_cannot_share_a_rank(self) -> None:
        with pytest.raises(ValueError, match="share a rank"):
            ResearchCycleResult(
                cycle_id=uuid4(),
                generated_at=NOW,
                proposals=(_proposal(1), _proposal(1, "INFY")),
            )

    def test_no_ideas_without_a_reason_raises(self) -> None:
        """An empty list with no reason is indistinguishable from a crash."""
        with pytest.raises(ValueError, match="must say why"):
            ResearchCycleResult(cycle_id=uuid4(), generated_at=NOW)

    def test_no_ideas_with_a_reason_is_a_valid_result(self) -> None:
        result = ResearchCycleResult(
            cycle_id=uuid4(),
            generated_at=NOW,
            no_ideas_reason="IV rank unavailable for the whole universe",
            rejected=(
                RejectedCandidate(
                    instrument_symbol="RELIANCE",
                    killed_by="iv_rank_available",
                    measured="no rank",
                    threshold="a rank is required",
                ),
            ),
        )

        assert not result.has_ideas
        assert len(result.rejected) == 1

    def test_two_ideas_come_back_in_rank_order(self) -> None:
        result = ResearchCycleResult(
            cycle_id=uuid4(),
            generated_at=NOW,
            proposals=(_proposal(2, "INFY"), _proposal(1)),
        )

        assert [item.rank for item in result.ranked()] == [1, 2]
        assert len(result.proposals) <= MAX_PROPOSALS_PER_CYCLE
