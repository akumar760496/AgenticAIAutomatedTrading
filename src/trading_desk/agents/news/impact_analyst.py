"""What a confirmed catalyst means for one company's shares.

Runs on the triage tier too. It is a judgement, but a narrow one with three
fields, and the synthesis model is reserved for writing the briefing.

**The response model has no price, no size and no quantity**, and that is
enforced by the schema rather than by the prompt asking nicely. CLAUDE.md rule
1 says an LLM never sizes an order; the cheapest way to keep that true is to
give it no field to put a number in. `verify 3_2` introspects the model to
confirm no such field has crept back.

`already_priced` is asked of the model but **not trusted as the gate**. Setup A
measures the move in ATR units and decides for itself (`domain/setups/setup_a.py`);
this field is an opinion that goes in the briefing next to the measurement, so
a reader can see when the two disagree.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, Field

from trading_desk.agents.news.prompts import IMPACT_SYSTEM, fenced_document
from trading_desk.domain.catalysts import CatalystLabel
from trading_desk.domain.enums import Direction
from trading_desk.domain.llm_cost import ModelTier
from trading_desk.domain.news import NewsArticle
from trading_desk.ports.llm import GovernedLlmPort

NODE = "impact_analyst"

MIN_HORIZON_DAYS = 1
MAX_HORIZON_DAYS = 30


class ImpactResponse(BaseModel):
    """Deliberately three fields. No price, no size, no quantity."""

    direction: Direction
    horizon_days: int = Field(ge=MIN_HORIZON_DAYS, le=MAX_HORIZON_DAYS)
    already_priced: bool
    supporting_quote: str = Field(default="", max_length=600)
    reasoning: str = Field(default="", max_length=400)


@dataclass(frozen=True)
class ImpactView:
    article: NewsArticle
    symbol: str
    catalyst: CatalystLabel
    direction: Direction
    horizon_days: int
    model_thinks_already_priced: bool
    supporting_quote: str
    reasoning: str


class ImpactAnalyst:
    def __init__(self, llm: GovernedLlmPort) -> None:
        self._llm = llm

    def assess(
        self, article: NewsArticle, symbol: str, catalyst: CatalystLabel, cycle_id: str
    ) -> ImpactView:
        document = fenced_document(article.headline, article.body, article.publisher)
        question = (
            f"Company: {symbol}\n"
            f"Catalyst type already established: {catalyst.catalyst_type.value}\n\n"
            f"{document.fenced()}"
        )
        result = self._llm.complete(
            node=NODE,
            cycle_id=cycle_id,
            tier=ModelTier.TRIAGE,
            system_prompt=IMPACT_SYSTEM,
            user_content=question,
            response_model=ImpactResponse,
        )
        response = result.value
        return ImpactView(
            article=article,
            symbol=symbol,
            catalyst=catalyst,
            direction=response.direction,
            horizon_days=response.horizon_days,
            model_thinks_already_priced=response.already_priced,
            supporting_quote=response.supporting_quote,
            reasoning=response.reasoning,
        )
