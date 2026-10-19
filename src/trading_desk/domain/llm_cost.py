"""What a model call costs, and whether we are allowed to make it.

Pure arithmetic. No client, no clock, no database - the spend figure and the
current time arrive as arguments.

Two decisions shape this module.

**The cap refuses before the call, on an estimate.** The true cost of a call is
only known once it has returned, and by then the money is spent. So the budget
check runs against an *over*-estimate of what the call will cost, and a call
that might cross the cap is refused. `docs/10` step 3.1 is explicit that the cap
must refuse rather than alert afterwards; alerting afterwards is a receipt, not
a limit.

**Rupees are the unit, not dollars.** Anthropic bills in USD, the cap is a rupee
figure Ajay set, and the briefing shows rupees. Converting once, here, at a rate
that lives in config means there is exactly one place where an FX assumption can
be wrong - and `LlmPricing.verified` says out loud whether anyone has checked it.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum

from trading_desk.domain.values import Money

TOKENS_PER_MILLION = Decimal("1000000")

# Characters per token for the pre-call estimate. English averages nearer 4, so
# 3 deliberately over-counts: under-estimating lets a call through that breaches
# the cap, which is the one failure this module exists to prevent.
CHARS_PER_TOKEN_ESTIMATE = Decimal("3")

# What we assume a response will cost when we cannot know it yet. Over-stated
# for the same reason, and only used for the pre-call check.
ASSUMED_OUTPUT_TOKENS = 2000


class ModelTier(StrEnum):
    """Which model a job gets.

    Tiering is a cost decision made per job, not per agent: deduping a thousand
    headlines on the synthesis model would cost more than the ideas are worth,
    and writing the briefing on the triage model reads like it.
    """

    TRIAGE = "triage"
    SYNTHESIS = "synthesis"


@dataclass(frozen=True)
class ModelPrice:
    """USD per million tokens, as Anthropic publishes them."""

    model: str
    input_usd_per_million: Decimal
    output_usd_per_million: Decimal


@dataclass(frozen=True)
class LlmPricing:
    """The full pricing picture: what each tier costs and what the cap is."""

    tiers: dict[ModelTier, ModelPrice]
    usd_to_inr: Decimal
    monthly_cap: Money
    verified: bool
    source: str

    def price_for(self, tier: ModelTier) -> ModelPrice:
        if tier not in self.tiers:
            raise KeyError(
                f"No price configured for tier {tier}. Add it to config/llm_pricing.yaml"
            )
        return self.tiers[tier]

    def model_for(self, tier: ModelTier) -> str:
        return self.price_for(tier).model


def cost_of_call(
    pricing: LlmPricing, tier: ModelTier, input_tokens: int, output_tokens: int
) -> Money:
    """Rupee cost of one call, rounded to the paise.

    Rounding half-up at the paise means a long cycle of tiny calls accumulates a
    few paise of over-statement rather than under-statement. We would rather the
    ledger read slightly high than slightly low against a hard cap.
    """
    if input_tokens < 0 or output_tokens < 0:
        raise ValueError(f"Token counts cannot be negative: {input_tokens} in, {output_tokens} out")

    price = pricing.price_for(tier)
    usd = (
        Decimal(input_tokens) * price.input_usd_per_million
        + Decimal(output_tokens) * price.output_usd_per_million
    ) / TOKENS_PER_MILLION
    paise = (usd * pricing.usd_to_inr * 100).to_integral_value(ROUND_HALF_UP)
    return Money(paise=int(paise))


def estimate_input_tokens(system_prompt: str, user_content: str) -> int:
    """A deliberately pessimistic token count for the pre-call budget check.

    Not a tokeniser. A tokeniser would be more accurate and would also mean
    importing a model-specific library into `domain/`, which rule 3 forbids and
    which would not change any decision this number feeds: the check only needs
    to be wrong in the safe direction.
    """
    characters = Decimal(len(system_prompt) + len(user_content))
    return int((characters / CHARS_PER_TOKEN_ESTIMATE).to_integral_value(ROUND_HALF_UP)) + 1


def estimate_cost(
    pricing: LlmPricing, tier: ModelTier, system_prompt: str, user_content: str
) -> Money:
    """What the call will cost at worst, for the cap check."""
    return cost_of_call(
        pricing,
        tier,
        input_tokens=estimate_input_tokens(system_prompt, user_content),
        output_tokens=ASSUMED_OUTPUT_TOKENS,
    )


@dataclass(frozen=True)
class CapVerdict:
    """The budget decision, with its numbers (CLAUDE.md rule 9).

    `allowed` alone would be a boolean, and a boolean cannot tell you whether
    you were refused by ten paise or by ten thousand rupees.
    """

    allowed: bool
    spent: Money
    cap: Money
    estimated: Money
    reason: str

    @property
    def headroom(self) -> Money:
        return self.cap - self.spent


def check_cap(spent: Money, cap: Money, estimated: Money) -> CapVerdict:
    """Would this call take the month past its budget?

    The comparison is against spend *plus* the estimate, not spend alone. A check
    that only asks "are we already over?" always allows one more call, and that
    call is unbounded in size.
    """
    projected = spent + estimated
    if projected.paise > cap.paise:
        return CapVerdict(
            allowed=False,
            spent=spent,
            cap=cap,
            estimated=estimated,
            reason=(
                f"refused: {spent} spent this month plus an estimated {estimated} "
                f"would reach {projected}, over the {cap} cap"
            ),
        )
    return CapVerdict(
        allowed=True,
        spent=spent,
        cap=cap,
        estimated=estimated,
        reason=f"allowed: {spent} spent of {cap}, this call estimated at {estimated}",
    )


class MonthlyCapExceededError(RuntimeError):
    """A call was refused because it would cross the month's budget.

    An exception rather than a returned `None`, because every caller would have
    to remember to check the None and one of them would not. Fails closed
    (CLAUDE.md rule 6): the cycle stops and produces no briefing, which is the
    correct outcome - a briefing written from half the agents is worse than
    silence, and the user can tell the difference between no message and a
    thin one.
    """

    def __init__(self, verdict: CapVerdict) -> None:
        super().__init__(verdict.reason)
        self.verdict = verdict
