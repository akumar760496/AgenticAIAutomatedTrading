"""How much to trust a story, from documented rules.

**Deliberately not an LLM.** `docs/10` asks for "credibility scores from
documented rules, each score carrying its rule", and `docs/12` asks Ajay to read
twenty of them and say whether he agrees. Neither is possible if the number came
out of a model: you cannot audit a score whose reasoning differs each time it is
computed, and "the model thought 0.62" is not a rule anyone can disagree with.

So this is arithmetic over a source tier and some phrasing patterns. Boring, and
that is the point - the same article scores the same number today and in March,
and every contribution is listed with the text that triggered it.

**The hedging cap is the load-bearing rule.** `docs/06`: "sources say" stories
are capped below 0.7 and cannot trigger alone. That is a ceiling applied last,
not a penalty that a pile of bonuses can climb back over.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from trading_desk.domain.enums import SourceTier

# Where a story starts, before anything about how it is written.
BASE_BY_TIER: dict[SourceTier, float] = {
    SourceTier.EXCHANGE: 1.00,
    SourceTier.TIER_1: 0.75,
    SourceTier.TIER_2: 0.55,
    SourceTier.UNVERIFIED: 0.30,
}

# Setup A's threshold. A hedged story is held below it by construction.
HEDGED_CEILING = 0.65

MAX_CORROBORATION_BONUS = 0.15
PER_PUBLISHER_BONUS = 0.05
ATTRIBUTION_BONUS = 0.10
FILING_REFERENCE_BONUS = 0.10

# Phrasings that mean "we could not confirm this". Each is paired with the
# words a reader would point at if asked why the score is low.
HEDGING_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"sources?\s+say", "sources say"),
    (r"people\s+(familiar|aware)\s+with", "people familiar with the matter"),
    (r"\breportedly\b", "reportedly"),
    (r"\brumou?r(s|ed)?\b", "rumour"),
    (r"\bspeculat(e|ed|ion|ing)\b", "speculation"),
    (r"could\s+not\s+be\s+(independently\s+)?(confirmed|verified)", "could not be confirmed"),
    (r"did\s+not\s+respond\s+to", "no response from the company"),
    (r"\bunconfirmed\b", "unconfirmed"),
)

# Phrasings that mean someone put their name to it.
ATTRIBUTION_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"in\s+a\s+(statement|press\s+release)", "a company statement"),
    (r"said\s+in\s+an?\s+(exchange\s+)?filing", "an exchange filing"),
    (r"(chief\s+executive|managing\s+director|chairman|cfo|ceo)\s+\w+\s+said", "a named executive"),
    (r"the\s+company\s+said", "the company itself"),
)

FILING_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\b(bse|nse)\s+filing", "a stock-exchange filing"),
    (r"regulatory\s+filing", "a regulatory filing"),
    (r"\bsebi\b", "a SEBI document"),
)


@dataclass(frozen=True)
class AppliedRule:
    """One contribution to a score, and the words behind it."""

    name: str
    delta: float
    evidence: str

    def describe(self) -> str:
        sign = "+" if self.delta >= 0 else ""
        return f"{self.name} ({sign}{self.delta:.2f}): {self.evidence}"


@dataclass(frozen=True)
class CredibilityScore:
    """A score you can argue with, because it shows its working."""

    value: float
    rules: tuple[AppliedRule, ...]
    was_capped: bool

    def explain(self) -> str:
        lines = [rule.describe() for rule in self.rules]
        if self.was_capped:
            lines.append(f"ceiling applied: hedged reporting cannot exceed {HEDGED_CEILING:.2f}")
        return f"{self.value:.2f} — " + "; ".join(lines)


def _matches(text: str, patterns: tuple[tuple[str, str], ...]) -> list[str]:
    found = []
    for pattern, description in patterns:
        if re.search(pattern, text, flags=re.IGNORECASE):
            found.append(description)
    return found


def score_credibility(
    headline: str,
    body: str,
    source_tier: SourceTier,
    corroborating_publishers: int = 0,
) -> CredibilityScore:
    """Score one story.

    `corroborating_publishers` is the count of *other* outlets carrying the same
    event, which the deduper already works out. Counted as distinct publishers
    rather than articles: one outlet running a story three times is one source,
    and treating it as three is how an echo becomes consensus.
    """
    text = f"{headline}\n{body}"
    base = BASE_BY_TIER[source_tier]
    rules = [AppliedRule("source tier", base, f"{source_tier.value} baseline")]

    # An exchange filing is the primary document. Nothing about how a filing is
    # worded makes it less true, so the rules below simply do not apply to it.
    if source_tier is SourceTier.EXCHANGE:
        return CredibilityScore(value=base, rules=tuple(rules), was_capped=False)

    hedges = _matches(text, HEDGING_PATTERNS)
    attributions = _matches(text, ATTRIBUTION_PATTERNS)
    filings = _matches(text, FILING_PATTERNS)

    value = base
    if hedges:
        rules.append(AppliedRule("hedged attribution", -0.25, ", ".join(hedges)))
        value -= 0.25
    if attributions:
        rules.append(AppliedRule("named attribution", ATTRIBUTION_BONUS, ", ".join(attributions)))
        value += ATTRIBUTION_BONUS
    if filings:
        rules.append(AppliedRule("cites a filing", FILING_REFERENCE_BONUS, ", ".join(filings)))
        value += FILING_REFERENCE_BONUS
    if corroborating_publishers > 0:
        bonus = min(corroborating_publishers * PER_PUBLISHER_BONUS, MAX_CORROBORATION_BONUS)
        rules.append(
            AppliedRule(
                "corroboration",
                bonus,
                f"{corroborating_publishers} other publisher(s) carried it",
            )
        )
        value += bonus

    # Last, and it cannot be bought back. docs/06: a hedged story must not
    # reach Setup A's 0.7 on the strength of bonuses.
    capped = bool(hedges) and value > HEDGED_CEILING
    if capped:
        value = HEDGED_CEILING

    return CredibilityScore(value=max(0.0, min(1.0, value)), rules=tuple(rules), was_capped=capped)
