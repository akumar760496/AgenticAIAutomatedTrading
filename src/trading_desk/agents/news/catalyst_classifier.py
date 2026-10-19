"""The catalyst classifier: one article in, one typed label out.

Runs on the triage tier. There are thousands of articles a month and this job
is mechanical - classifying them on the synthesis model would cost more than
the ideas are worth (`domain/llm_cost.py` measures the gap at 50x).

Three things happen here that are not the model's decision:

1. **The response schema only admits the closed vocabulary.** An eighth
   catalyst type cannot be returned, because `CatalystType` has seven and a
   NONE, and anything else fails validation.
2. **The evidence quote is checked against the article.** A quote that is not
   in the text means the model wrote the sentence rather than found it, and the
   label is downgraded to NONE with the reason recorded. This catches a
   confident hallucination without needing to judge whether it was right.
3. **A suspicious document is still classified**, and the suspicion travels
   with the result. Dropping it would also drop the evidence that someone tried.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, Field

from trading_desk.agents.news.prompts import CLASSIFIER_SYSTEM, fenced_document
from trading_desk.domain.catalysts import CatalystLabel, CatalystType, quote_is_grounded
from trading_desk.domain.enums import SourceTier
from trading_desk.domain.filing_subjects import route
from trading_desk.domain.llm_cost import ModelTier
from trading_desk.domain.news import NewsArticle
from trading_desk.ports.llm import GovernedLlmPort

NODE = "catalyst_classifier"


class CatalystResponse(BaseModel):
    """What the model is allowed to say. The schema is the guard rail."""

    catalyst_type: CatalystType
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_quote: str = Field(default="")
    reasoning: str = Field(default="", max_length=400)


@dataclass(frozen=True)
class ClassifiedArticle:
    """An article with its label and everything worth auditing about it."""

    article: NewsArticle
    label: CatalystLabel
    injection_suspicions: tuple[str, ...]
    quote_was_grounded: bool
    decided_by_subject: bool = False
    """True when the exchange's own subject line settled it and no model ran."""

    @property
    def is_suspicious(self) -> bool:
        return bool(self.injection_suspicions)


class CatalystClassifier:
    def __init__(self, llm: GovernedLlmPort) -> None:
        self._llm = llm

    def classify(self, article: NewsArticle, cycle_id: str) -> ClassifiedArticle:
        from_subject = self._by_subject(article)
        if from_subject is not None:
            return from_subject

        document = fenced_document(article.headline, article.body, article.publisher)
        result = self._llm.complete(
            node=NODE,
            cycle_id=cycle_id,
            tier=ModelTier.TRIAGE,
            system_prompt=CLASSIFIER_SYSTEM,
            user_content=document.fenced(),
            response_model=CatalystResponse,
        )
        response = result.value

        if response.catalyst_type is CatalystType.NONE:
            return ClassifiedArticle(
                article=article,
                label=CatalystLabel(
                    catalyst_type=CatalystType.NONE,
                    confidence=response.confidence,
                    evidence_quote="",
                    reasoning=response.reasoning,
                ),
                injection_suspicions=document.suspicions,
                quote_was_grounded=True,  # nothing was claimed, so nothing is ungrounded
            )

        source_text = f"{article.headline}\n{article.body}"
        grounded = quote_is_grounded(response.evidence_quote, source_text)
        if not grounded:
            return ClassifiedArticle(
                article=article,
                label=CatalystLabel(
                    catalyst_type=CatalystType.NONE,
                    confidence=0.0,
                    evidence_quote="",
                    reasoning=(
                        f"downgraded from {response.catalyst_type.value}: the quoted "
                        "evidence does not appear in the article"
                    ),
                ),
                injection_suspicions=document.suspicions,
                quote_was_grounded=False,
            )

        return ClassifiedArticle(
            article=article,
            label=CatalystLabel(
                catalyst_type=response.catalyst_type,
                confidence=response.confidence,
                evidence_quote=response.evidence_quote,
                reasoning=response.reasoning,
            ),
            injection_suspicions=document.suspicions,
            quote_was_grounded=True,
        )

    def _by_subject(self, article: NewsArticle) -> ClassifiedArticle | None:
        """Let the exchange's own classification stand where it is decisive.

        An NSE filing carries a subject from a controlled vocabulary, and for
        60% of the archive that subject *is* the answer - measured, 2026-10-19.
        Paying a model to re-derive "Credit Rating" means a worse answer at a
        cost, and this one can be read and corrected by a human.

        Only filings. A journalist's headline is free text and decides nothing.
        """
        if article.source_tier is not SourceTier.EXCHANGE:
            return None

        decided = route(article.headline)
        if decided is None:
            return None  # the subject does not settle it; the model reads it

        return ClassifiedArticle(
            article=article,
            label=CatalystLabel(
                catalyst_type=decided,
                confidence=1.0,
                # The subject line is the evidence, and it is verbatim from the
                # exchange, so it is grounded by construction.
                evidence_quote="" if decided is CatalystType.NONE else article.headline,
                reasoning=f"exchange filing subject: {article.headline}",
            ),
            injection_suspicions=(),
            quote_was_grounded=True,
            decided_by_subject=True,
        )
