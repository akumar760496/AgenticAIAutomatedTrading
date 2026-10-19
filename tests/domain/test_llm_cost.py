"""Cost arithmetic and the cap decision.

The test that matters most is `TestTheCapRefusesBeforeTheCall`: a check that
only looks at money already spent always lets one more unbounded call through.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from trading_desk.domain.llm_cost import (
    ASSUMED_OUTPUT_TOKENS,
    CapVerdict,
    LlmPricing,
    ModelPrice,
    ModelTier,
    MonthlyCapExceededError,
    check_cap,
    cost_of_call,
    estimate_cost,
    estimate_input_tokens,
)
from trading_desk.domain.values import Money


def _pricing(cap_rupees: str = "2000") -> LlmPricing:
    return LlmPricing(
        tiers={
            ModelTier.TRIAGE: ModelPrice(
                model="claude-haiku-5-5",
                input_usd_per_million=Decimal("0.10"),
                output_usd_per_million=Decimal("0.50"),
            ),
            ModelTier.SYNTHESIS: ModelPrice(
                model="claude-opus-5",
                input_usd_per_million=Decimal("5.00"),
                output_usd_per_million=Decimal("25.00"),
            ),
        },
        usd_to_inr=Decimal("89"),
        monthly_cap=Money.from_rupees(Decimal(cap_rupees)),
        verified=False,
        source="test",
    )


class TestCostArithmetic:
    def test_a_synthesis_call_is_hand_checkable(self) -> None:
        # 1,000,000 input at $5 + 100,000 output at $25/M = $5 + $2.50 = $7.50.
        # At 89 INR/USD that is 667.50 rupees = 66,750 paise.
        cost = cost_of_call(_pricing(), ModelTier.SYNTHESIS, 1_000_000, 100_000)

        assert cost == Money.from_rupees(Decimal("667.50"))

    def test_triage_is_fifty_times_cheaper_than_synthesis(self) -> None:
        """The whole reason tiering exists. If this ever stops being true, the
        tier split is costing complexity for nothing.

        Token counts are large on purpose: at 100k input the triage cost is
        133.5 paise, and rounding that to 134 breaks an exact ratio for a
        reason that has nothing to do with the prices.
        """
        pricing = _pricing()
        triage = cost_of_call(pricing, ModelTier.TRIAGE, 1_000_000, 100_000)
        synthesis = cost_of_call(pricing, ModelTier.SYNTHESIS, 1_000_000, 100_000)

        assert synthesis.paise == triage.paise * 50

    def test_a_zero_token_call_costs_nothing(self) -> None:
        assert cost_of_call(_pricing(), ModelTier.TRIAGE, 0, 0) == Money.zero()

    def test_negative_tokens_are_rejected_not_silently_absolute(self) -> None:
        with pytest.raises(ValueError, match="cannot be negative"):
            cost_of_call(_pricing(), ModelTier.TRIAGE, -1, 10)

    def test_an_unpriced_tier_raises_rather_than_costing_zero(self) -> None:
        """A tier with no price must not quietly cost nothing - that is how a
        model gets called ten thousand times outside the budget."""
        pricing = LlmPricing(
            tiers={}, usd_to_inr=Decimal("89"), monthly_cap=Money.zero(), verified=False, source=""
        )

        with pytest.raises(KeyError, match="No price configured"):
            cost_of_call(pricing, ModelTier.TRIAGE, 100, 100)

    def test_cost_is_money_not_a_float(self) -> None:
        cost = cost_of_call(_pricing(), ModelTier.SYNTHESIS, 12_345, 6_789)

        assert isinstance(cost, Money)
        assert isinstance(cost.paise, int)


class TestTheEstimateErrsHigh:
    def test_the_token_estimate_over_counts_rather_than_under(self) -> None:
        """English runs nearer 4 characters a token. 3 is chosen so the pre-call
        check is wrong in the safe direction."""
        text = "a" * 400

        assert estimate_input_tokens("", text) > 100  # a 4-char/token count

    def test_the_estimate_assumes_a_full_length_response(self) -> None:
        pricing = _pricing()
        estimated = estimate_cost(pricing, ModelTier.SYNTHESIS, "system", "hello")
        output_alone = cost_of_call(pricing, ModelTier.SYNTHESIS, 0, ASSUMED_OUTPUT_TOKENS)

        assert estimated.paise >= output_alone.paise


class TestTheCapRefusesBeforeTheCall:
    def test_a_call_that_fits_is_allowed(self) -> None:
        verdict = check_cap(
            spent=Money.from_rupees(Decimal("100")),
            cap=Money.from_rupees(Decimal("2000")),
            estimated=Money.from_rupees(Decimal("5")),
        )

        assert verdict.allowed
        assert verdict.headroom == Money.from_rupees(Decimal("1900"))

    def test_the_estimate_counts_toward_the_cap_not_just_what_is_spent(self) -> None:
        """The point of the module. Spend is under the cap, but this call would
        cross it, so it is refused *now* rather than reported afterwards."""
        verdict = check_cap(
            spent=Money.from_rupees(Decimal("1999")),
            cap=Money.from_rupees(Decimal("2000")),
            estimated=Money.from_rupees(Decimal("50")),
        )

        assert not verdict.allowed
        assert "would reach" in verdict.reason

    def test_landing_exactly_on_the_cap_is_allowed(self) -> None:
        verdict = check_cap(
            spent=Money.from_rupees(Decimal("1950")),
            cap=Money.from_rupees(Decimal("2000")),
            estimated=Money.from_rupees(Decimal("50")),
        )

        assert verdict.allowed

    def test_one_paise_over_the_cap_is_refused(self) -> None:
        verdict = check_cap(
            spent=Money(paise=195000), cap=Money(paise=200000), estimated=Money(paise=5001)
        )

        assert not verdict.allowed

    def test_the_verdict_carries_its_numbers_not_just_a_boolean(self) -> None:
        """CLAUDE.md rule 9. A refusal you cannot size is a refusal you cannot act on."""
        verdict = check_cap(
            Money.from_rupees(Decimal("10")), Money.from_rupees(Decimal("5")), Money.zero()
        )

        assert verdict.spent == Money.from_rupees(Decimal("10"))
        assert verdict.cap == Money.from_rupees(Decimal("5"))
        assert "₹10.00" in verdict.reason and "₹5.00" in verdict.reason


def test_the_refusal_exception_carries_the_verdict() -> None:
    verdict = CapVerdict(
        allowed=False,
        spent=Money.from_rupees(Decimal("2000")),
        cap=Money.from_rupees(Decimal("2000")),
        estimated=Money.from_rupees(Decimal("1")),
        reason="refused: over budget",
    )
    error = MonthlyCapExceededError(verdict)

    assert error.verdict is verdict
    assert "over budget" in str(error)
