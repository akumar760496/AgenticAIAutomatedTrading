"""What counts as a catalyst, and what one classification looks like.

**The vocabulary is closed.** `config/setups.yaml` says it plainly: an LLM will
happily call anything a catalyst, so an unlisted type fails rather than being
judged. `CatalystType` is that list as an enum, and a model that returns
anything else cannot be parsed into one - the response fails validation and the
cycle stops, rather than inventing an eighth category nobody gated on.

`NONE` is a real answer, not a failure. Most news is not a catalyst, and a
classifier with no way to say so will force every item into the nearest bucket.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class CatalystType(StrEnum):
    """The seven types Setup A trades, plus "not a catalyst".

    Kept in step with `config/setups.yaml`; a mismatch is caught by
    `verify 3_2` rather than discovered when a gate silently never fires.
    """

    EARNINGS_SURPRISE = "EARNINGS_SURPRISE"
    GUIDANCE_CHANGE = "GUIDANCE_CHANGE"
    LARGE_ORDER_WIN = "LARGE_ORDER_WIN"
    REGULATORY_DECISION = "REGULATORY_DECISION"
    MERGER_OR_ACQUISITION = "MERGER_OR_ACQUISITION"
    CREDIT_RATING_ACTION = "CREDIT_RATING_ACTION"
    MANAGEMENT_EXIT = "MANAGEMENT_EXIT"
    NONE = "NONE"


# The types a setup may act on. NONE is deliberately absent.
TRADABLE_CATALYSTS = tuple(item for item in CatalystType if item is not CatalystType.NONE)


@dataclass(frozen=True)
class CatalystLabel:
    """One classification, with the sentence it came from.

    `evidence_quote` is required and must appear in the source text. A label
    without a quote cannot be checked by a human in ten seconds, and a label
    nobody checks is a label nobody should trust. It also makes a hallucinated
    classification visible: a quote that is not in the article is caught by
    `quote_is_grounded` rather than read as a finding.
    """

    catalyst_type: CatalystType
    confidence: float
    evidence_quote: str
    reasoning: str = ""

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence must be between 0 and 1, got {self.confidence}")
        if self.catalyst_type is not CatalystType.NONE and not self.evidence_quote.strip():
            raise ValueError(f"a {self.catalyst_type} label needs an evidence quote")

    @property
    def is_tradable(self) -> bool:
        return self.catalyst_type is not CatalystType.NONE


def quote_is_grounded(quote: str, source_text: str) -> bool:
    """Does the evidence quote actually appear in the article?

    Compared on collapsed whitespace and ignoring case, because a model will
    re-wrap a line it copied faithfully. Anything looser would start accepting
    paraphrases, and a paraphrase is exactly what this is here to catch.
    """
    if not quote.strip():
        return False
    needle = " ".join(quote.split()).casefold()
    haystack = " ".join(source_text.split()).casefold()
    return needle in haystack
