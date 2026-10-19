"""Running one research cycle: gather the inputs, run the graph, record it.

The graph decides nothing about where its inputs come from - it is handed
articles, an IV rank per symbol and a set of holidays. This is where those come
from, because reading files and databases is the application layer's job and
`agents/` may not do it.

Two things happen here that are not the graph's business:

**The budget is checked before the cycle starts, not during it.** A cycle that
runs five specialists and then cannot afford to write the briefing has produced
nothing, and the reason would surface as an exception rather than as a result.
`budget_check` answers it up front and a refusal becomes a "no ideas" result
with the cap quoted (rule 6).

**Filings are read alongside articles.** They are the primary catalyst source -
an order win arrives as a filing first and as journalism afterwards - and the
news agent takes both in one shape (`domain/news.filing_as_article`).

**Resuming is `run` with the same request.** There is no separate resume path
to get wrong: the same `cycle_id` makes the response cache hit, the free work
re-runs, and the answer comes out the same.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from uuid import UUID, uuid4

from trading_desk.agents.strategist.graph import CycleOutcome, CycleRequest, StrategistGraph
from trading_desk.application.llm_governor import CostGovernedLlm, CycleCostReport
from trading_desk.domain.instruments import InstrumentRef
from trading_desk.domain.llm_cost import ModelTier
from trading_desk.domain.news import NewsArticle, filing_as_article
from trading_desk.domain.proposals import ResearchCycleResult, TradeProposal
from trading_desk.domain.values import Money
from trading_desk.domain.vol import IvRank
from trading_desk.ports.clock import Clock
from trading_desk.ports.journal import JournalPort, RecordedIdea
from trading_desk.ports.news_store import NewsStorePort

# How much of the archive one cycle reads. Generous: dedup and entity linking
# drop most of it before anything is paid for, and a window that is too small
# loses a Friday evening filing on a Monday morning run.
DEFAULT_ARTICLE_LIMIT = 500

# Only used to ask the cap whether a cycle can start at all. Two synthesis
# calls is the most a cycle can make; this stands in for their prompt size.
BUDGET_PROBE = "x" * 8000


@dataclass(frozen=True)
class CycleOutput:
    """The cycle's result, how it was reached, and what it cost."""

    result: ResearchCycleResult
    costs: CycleCostReport
    outcome: CycleOutcome | None
    articles_read: int

    @property
    def ran(self) -> bool:
        """False when the cap refused before any work started."""
        return self.outcome is not None


class ResearchCycle:
    def __init__(
        self,
        *,
        graph: StrategistGraph,
        governor: CostGovernedLlm,
        journal: JournalPort,
        news_store: NewsStorePort,
        clock: Clock,
        article_limit: int = DEFAULT_ARTICLE_LIMIT,
    ) -> None:
        self._graph = graph
        self._governor = governor
        self._journal = journal
        self._news = news_store
        self._clock = clock
        self._limit = article_limit

    def build_request(
        self,
        *,
        universe: tuple[InstrumentRef, ...],
        iv_ranks: dict[str, IvRank],
        holidays: frozenset[date] = frozenset(),
        banned_symbols: frozenset[str] = frozenset(),
        cycle_id: UUID | None = None,
    ) -> CycleRequest:
        """Read the archive and assemble one cycle's inputs."""
        return CycleRequest(
            cycle_id=cycle_id or uuid4(),
            as_of=self._clock.utc_now(),
            universe=universe,
            articles=self.read_archive(),
            iv_ranks=iv_ranks,
            holidays=holidays,
            banned_symbols=banned_symbols,
        )

    def run(self, request: CycleRequest) -> CycleOutput:
        affordable = self._governor.budget_check(ModelTier.SYNTHESIS, BUDGET_PROBE, BUDGET_PROBE)
        if not affordable.allowed:
            return CycleOutput(
                result=ResearchCycleResult(
                    cycle_id=request.cycle_id,
                    generated_at=request.as_of,
                    no_ideas_reason=f"cycle not started: {affordable.reason}",
                ),
                costs=self._governor.cost_report(str(request.cycle_id)),
                outcome=None,
                articles_read=0,
            )

        outcome = self._graph.run(request)
        for proposal in outcome.result.proposals:
            self._journal.record_idea(
                _as_recorded_idea(
                    proposal,
                    cycle_id=request.cycle_id,
                    risk=outcome.risk_per_lot.get(proposal.instrument_symbol, Money.zero()),
                )
            )

        return CycleOutput(
            result=outcome.result,
            costs=self._governor.cost_report(str(request.cycle_id)),
            outcome=outcome,
            articles_read=len(request.articles),
        )

    def read_archive(self) -> list[NewsArticle]:
        """Articles and filings, in one shape, oldest first."""
        articles = list(self._news.all_articles(self._limit))
        filings = [filing_as_article(filing) for filing in self._news.all_filings(self._limit)]
        return articles + filings


def _as_recorded_idea(proposal: TradeProposal, *, cycle_id: UUID, risk: Money) -> RecordedIdea:
    """One proposal as the journal stores it.

    **`hypothetical_risk` is one lot's maximum loss, not a position's risk**,
    and a gate value says so in words. Position risk needs the account size,
    and the account size is blank (ISS-022), so the honest figure to store is
    the one that does not depend on it. When the capital figure arrives this
    becomes the risk gate's `size.total_risk` and the number changes meaning -
    which is why the label travels with it rather than living in a docstring.
    """
    gate_values = dict(proposal.gate_values)
    gate_values["hypothetical_risk_basis"] = "one lot's max loss (no capital figure, ISS-022)"
    structure = " / ".join(
        f"{leg.action.value} {leg.right.value} {leg.strike.rupees():g}" for leg in proposal.legs
    )
    return RecordedIdea(
        idea_id=proposal.proposal_id,
        cycle_id=cycle_id,
        symbol=proposal.instrument_symbol,
        setup_id=proposal.setup_id.value,
        direction=proposal.direction,
        rank=proposal.rank,
        confidence=proposal.thesis.confidence,
        gate_values=gate_values,
        hypothetical_risk=risk,
        created_at=proposal.created_at,
        structure=structure,
        invalidation=proposal.thesis.invalidation,
        agent_rationale=proposal.thesis.claim,
        used_iv_proxy=proposal.used_iv_proxy,
    )
