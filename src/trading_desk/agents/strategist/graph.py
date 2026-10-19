"""The Chief Strategist: one research cycle, six nodes, at most two ideas.

```
        news ──▶ [ fundamental | technical | options | portfolio ] ──▶ judge
                                                                        │
                           rank (at most 2) ──▶ synthesis ──▶ propose ◀──┘
```

Plain Python rather than LangGraph, sequential rather than threaded, and
resumable at the paid boundary rather than the node boundary. All three are
deliberate and the reasons are in `docs/state/DEVLOG.md` under 3.4 - the short
version is that the graph is a static DAG, that two concurrent model calls can
both pass a cap check neither would pass second, and that re-running arithmetic
costs nothing while re-running a model call costs money.

**Nothing in this module decides anything.** It calls specialists, hands their
measurements to `domain/setups/setup_a.py`, ranks what survives with
`domain/ranking.py`, and asks a model to write two sentences about the winners.
Every verdict in the result came out of a gate function.

**"No ideas" is the expected answer most days** (`docs/02` targets 40-60% of
sessions) and it is a first-class result carrying a reason, never an empty
list (FR-4.9). Every candidate that died is returned with the gate that killed
it (FR-4.10).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime
from uuid import UUID

from trading_desk.agents.fundamental.agent import FundamentalAgent, FundamentalView
from trading_desk.agents.news.pipeline import Catalyst, NewsAgent, PipelineReport
from trading_desk.agents.options.agent import OptionsAgent, OptionsView
from trading_desk.agents.portfolio.agent import PortfolioAgent, PortfolioView
from trading_desk.agents.strategist.candidates import (
    SymbolInputs,
    build_setup_a_candidate,
    facts_for_synthesis,
)
from trading_desk.agents.strategist.proposal_builder import build_proposal, build_thesis
from trading_desk.agents.strategist.synthesis import SynthesisWriter
from trading_desk.agents.technical.agent import TechnicalAgent, TechnicalView
from trading_desk.domain.cycle import CycleNode, NodeStatus
from trading_desk.domain.enums import SetupId
from trading_desk.domain.gates import GateReport
from trading_desk.domain.instruments import InstrumentRef
from trading_desk.domain.news import NewsArticle
from trading_desk.domain.proposals import (
    RejectedCandidate,
    ResearchCycleResult,
    TradeProposal,
)
from trading_desk.domain.ranking import Candidate, RankedCandidate, rank_candidates
from trading_desk.domain.sessions import sessions_between
from trading_desk.domain.setups.setup_a import evaluate as evaluate_setup_a
from trading_desk.domain.setups.thresholds import SetupThresholds
from trading_desk.domain.specialists import SpecialistFinding
from trading_desk.domain.timezones import to_ist
from trading_desk.domain.values import Money
from trading_desk.domain.vol import IvRank, IvSource, RankMethod
from trading_desk.ports.clock import Clock
from trading_desk.ports.cycle_store import CycleStore, NodeRun

# Said every run, not only when something is wrong. Both are inputs Setup A's
# calendar gate consults, both default to the permissive answer, and neither
# has a source wired (ISS-028).
MISSING_INPUTS = (
    "NSE F&O ban list: not fetched, so every symbol reads as not banned (ISS-028)",
    "earnings calendar: none exists, so 'no results inside the expiry window' is "
    "what the gate sees whether or not it is true (ISS-028)",
)


@dataclass(frozen=True)
class CycleRequest:
    """Everything one cycle needs, gathered by the caller.

    The calendar facts arrive as plain data - a set of holidays, a set of
    banned symbols - because an agent may not import `application`, which is
    where the files that produce them are read.
    """

    cycle_id: UUID
    as_of: datetime
    universe: tuple[InstrumentRef, ...]
    articles: list[NewsArticle]
    iv_ranks: dict[str, IvRank]
    holidays: frozenset[date] = frozenset()
    banned_symbols: frozenset[str] = frozenset()
    results_dates: dict[str, date] = field(default_factory=dict)
    # Anything else the caller knows it could not supply. The wiring adds to
    # this - production has gaps a test does not, and the briefing has to name
    # them rather than let an empty account read as a flat one.
    extra_missing_inputs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.as_of.tzinfo is None:
            raise ValueError(f"{self.cycle_id}: as_of must be tz-aware")


@dataclass(frozen=True)
class CycleOutcome:
    """The result, plus how it was arrived at.

    The result alone is what the briefing renders. Everything beside it is what
    makes the run auditable: which nodes ran, what each specialist measured,
    and how many paid calls were replayed rather than made.
    """

    result: ResearchCycleResult
    news: PipelineReport
    node_runs: tuple[NodeRun, ...]
    findings: tuple[SpecialistFinding, ...]
    missing_inputs: tuple[str, ...]
    gate_reports: tuple[GateReport, ...]
    # Max loss for ONE lot of each proposed structure. Not a position's risk:
    # that needs the account size, which is blank (ISS-022). Carried here so
    # the journal can store a number whose meaning does not depend on it.
    risk_per_lot: dict[str, Money] = field(default_factory=dict)

    def completed_nodes(self) -> tuple[str, ...]:
        return tuple(run.node for run in self.node_runs if run.status is NodeStatus.COMPLETED)


class StrategistGraph:
    def __init__(
        self,
        *,
        news: NewsAgent,
        fundamental: FundamentalAgent,
        technical: TechnicalAgent,
        options: OptionsAgent,
        portfolio: PortfolioAgent,
        synthesis: SynthesisWriter,
        store: CycleStore,
        clock: Clock,
        thresholds: SetupThresholds,
    ) -> None:
        self._news = news
        self._fundamental = fundamental
        self._technical = technical
        self._options = options
        self._portfolio = portfolio
        self._synthesis = synthesis
        self._store = store
        self._clock = clock
        self._thresholds = thresholds
        # Per-run state. One cycle at a time: this object is not thread-safe,
        # which is the same reason the fan-out is sequential (see the module
        # docstring). Reset at the top of `run` so a second cycle on the same
        # graph does not inherit the first one's node list.
        self._runs: list[NodeRun] = []
        self._findings: list[SpecialistFinding] = []

    def run(self, request: CycleRequest) -> CycleOutcome:
        self._runs = []
        self._findings = []

        report = self._run_news(request)
        inputs = self._gather(request, report)
        candidates, rejected, reports = self._judge(inputs)
        ranked, overflow = rank_candidates(candidates)
        rejected.extend(_overflow_rejections(overflow))

        proposals, synthesis_rejections = self._propose(request, ranked, inputs)
        rejected.extend(synthesis_rejections)

        return CycleOutcome(
            result=ResearchCycleResult(
                cycle_id=request.cycle_id,
                generated_at=request.as_of,
                proposals=tuple(proposals),
                rejected=tuple(rejected),
                no_ideas_reason=None if proposals else _no_ideas_reason(report, rejected),
            ),
            news=report,
            node_runs=tuple(self._runs),
            findings=tuple(self._findings),
            missing_inputs=MISSING_INPUTS + request.extra_missing_inputs,
            gate_reports=tuple(reports),
            risk_per_lot=_risk_per_lot(proposals, inputs),
        )

    # ----------------------------------------------------------------- nodes

    @contextmanager
    def _node(self, cycle_id: UUID, node: CycleNode) -> Iterator[dict[str, str]]:
        """Record STARTED before the work and COMPLETED or FAILED after it.

        STARTED first, so a crash leaves evidence. A node with a STARTED row
        and no COMPLETED row is "this never finished", which is a different
        thing from "this was never reached" and needs different handling.
        """
        measured: dict[str, str] = {}
        self._record(cycle_id, node, NodeStatus.STARTED, measured)
        try:
            yield measured
        except Exception as error:
            self._record(
                cycle_id, node, NodeStatus.FAILED, measured, detail=f"{type(error).__name__}"
            )
            raise
        self._record(cycle_id, node, NodeStatus.COMPLETED, measured)

    def _record(
        self,
        cycle_id: UUID,
        node: CycleNode,
        status: NodeStatus,
        measured: dict[str, str],
        detail: str = "",
    ) -> None:
        run = NodeRun(
            cycle_id=str(cycle_id),
            node=node.value,
            status=status,
            recorded_at=self._clock.utc_now(),
            detail=detail,
            measurements=dict(measured),
        )
        self._store.record_node(run)
        self._runs.append(run)

    def _run_news(self, request: CycleRequest) -> PipelineReport:
        with self._node(request.cycle_id, CycleNode.NEWS) as measured:
            report = self._news.run(request.articles, str(request.cycle_id))
            measured["articles_in"] = str(report.articles_in)
            measured["stories"] = str(report.stories_after_dedup)
            measured["catalysts"] = str(len(report.catalysts))
            measured["model_calls"] = str(report.llm_calls)
        return report

    def _gather(self, request: CycleRequest, report: PipelineReport) -> dict[str, SymbolInputs]:
        """The fan-out: four independent specialists over the catalyst symbols."""
        by_symbol = _catalysts_in_universe(report.catalysts, request.universe)
        ages = {
            symbol: _sessions_since(catalyst, request) for symbol, catalyst in by_symbol.items()
        }

        fundamental = self._run_fundamental(request, by_symbol)
        technical = self._run_technical(request, by_symbol, ages)
        options = self._run_options(request, by_symbol)
        portfolio = self._run_portfolio(request, by_symbol)

        instruments = {item.symbol: item for item in request.universe}
        return {
            symbol: SymbolInputs(
                instrument=instruments[symbol],
                catalyst=catalyst,
                fundamental=fundamental[symbol],
                technical=technical[symbol],
                options=options[symbol],
                portfolio=portfolio[symbol],
                iv_rank=_iv_rank_for(symbol, request),
                sessions_since_catalyst=ages[symbol],
                in_ban_list=symbol in request.banned_symbols,
                results_date_in_window=request.results_dates.get(symbol),
            )
            for symbol, catalyst in by_symbol.items()
        }

    def _run_fundamental(
        self, request: CycleRequest, by_symbol: dict[str, Catalyst]
    ) -> dict[str, FundamentalView]:
        with self._node(request.cycle_id, CycleNode.FUNDAMENTAL) as measured:
            as_of = to_ist(request.as_of).date()
            views = {symbol: self._fundamental.assess(symbol, as_of) for symbol in by_symbol}
            measured["symbols"] = str(len(views))
            measured["vetoed"] = str(sum(1 for v in views.values() if not v.finding.is_clear))
        self._findings.extend(view.finding for view in views.values())
        return views

    def _run_technical(
        self, request: CycleRequest, by_symbol: dict[str, Catalyst], ages: dict[str, int]
    ) -> dict[str, TechnicalView]:
        with self._node(request.cycle_id, CycleNode.TECHNICAL) as measured:
            views = {symbol: self._technical.assess(symbol, ages[symbol]) for symbol in by_symbol}
            measured["symbols"] = str(len(views))
            measured["with_full_history"] = str(
                sum(1 for v in views.values() if v.has_enough_history)
            )
        self._findings.extend(view.finding for view in views.values())
        return views

    def _run_options(
        self, request: CycleRequest, by_symbol: dict[str, Catalyst]
    ) -> dict[str, OptionsView]:
        instruments = {item.symbol: item for item in request.universe}
        with self._node(request.cycle_id, CycleNode.OPTIONS) as measured:
            views = {
                symbol: self._options.propose(
                    instrument=instruments[symbol],
                    setup=SetupId.CATALYST_CONTINUATION,
                    direction=catalyst.impact.direction,
                    iv_rank=_iv_rank_for(symbol, request),
                    as_of=to_ist(request.as_of).date(),
                    time_of_day=to_ist(request.as_of).time(),
                )
                for symbol, catalyst in by_symbol.items()
            }
            measured["symbols"] = str(len(views))
            measured["with_a_structure"] = str(sum(1 for v in views.values() if v.has_structure))
        self._findings.extend(view.finding for view in views.values())
        return views

    def _run_portfolio(
        self, request: CycleRequest, by_symbol: dict[str, Catalyst]
    ) -> dict[str, PortfolioView]:
        with self._node(request.cycle_id, CycleNode.PORTFOLIO) as measured:
            views = {symbol: self._portfolio.assess(symbol, request.as_of) for symbol in by_symbol}
            measured["symbols"] = str(len(views))
            if views:
                any_view = next(iter(views.values()))
                measured["open_positions"] = str(any_view.positions)
        self._findings.extend(view.finding for view in views.values())
        return views

    # ----------------------------------------------------------------- judge

    def _judge(
        self, inputs: dict[str, SymbolInputs]
    ) -> tuple[list[Candidate], list[RejectedCandidate], list[GateReport]]:
        """Run Setup A's gates. This is where every verdict is made."""
        survivors: list[Candidate] = []
        rejected: list[RejectedCandidate] = []
        reports: list[GateReport] = []

        for symbol, symbol_inputs in sorted(inputs.items()):
            candidate = build_setup_a_candidate(symbol_inputs)
            report = evaluate_setup_a(candidate, self._thresholds.setup_a)
            reports.append(report)

            if not report.approved:
                rejected.append(_rejection_from(report))
                continue

            survivors.append(
                Candidate(
                    symbol=symbol,
                    setup_id=SetupId.CATALYST_CONTINUATION,
                    direction=candidate.direction,
                    credibility=candidate.credibility or 0.0,
                    reward_to_risk=_reward_to_risk(symbol_inputs),
                    gate_values=report.values(),
                    used_iv_proxy=candidate.iv_rank_is_proxy,
                )
            )
        return survivors, rejected, reports

    # -------------------------------------------------------------- synthesis

    def _propose(
        self,
        request: CycleRequest,
        ranked: list[RankedCandidate],
        inputs: dict[str, SymbolInputs],
    ) -> tuple[list[TradeProposal], list[RejectedCandidate]]:
        """Write and build the surviving ideas. At most two model calls."""
        proposals: list[TradeProposal] = []
        rejected: list[RejectedCandidate] = []
        if not ranked:
            return proposals, rejected

        with self._node(request.cycle_id, CycleNode.SYNTHESIS) as measured:
            for item in ranked:
                symbol_inputs = inputs[item.candidate.symbol]
                outcome = self._one_proposal(request, item.rank, item.candidate, symbol_inputs)
                if isinstance(outcome, RejectedCandidate):
                    rejected.append(outcome)
                else:
                    proposals.append(outcome)
            measured["written"] = str(len(ranked))
            measured["kept"] = str(len(proposals))
        return proposals, rejected

    def _one_proposal(
        self,
        request: CycleRequest,
        rank: int,
        candidate: Candidate,
        symbol_inputs: SymbolInputs,
    ) -> TradeProposal | RejectedCandidate:
        facts = facts_for_synthesis(symbol_inputs, candidate.gate_values)
        written = self._synthesis.write(
            facts=facts, symbol=candidate.symbol, cycle_id=str(request.cycle_id)
        )
        if not written.is_grounded:
            return RejectedCandidate(
                instrument_symbol=candidate.symbol,
                killed_by="synthesis_grounding",
                measured=f"figures not in the facts: {', '.join(written.invented_numbers)}",
                threshold="every number written must appear in the supplied facts",
            )

        try:
            thesis = build_thesis(symbol_inputs, candidate, written, request.as_of)
        except ValueError as error:
            return RejectedCandidate(
                instrument_symbol=candidate.symbol,
                killed_by="independent_sources",
                measured=str(error),
                threshold="2 independent sources (FR-4.6)",
            )

        return build_proposal(
            cycle_id=request.cycle_id,
            rank=rank,
            inputs=symbol_inputs,
            candidate=candidate,
            thesis=thesis,
            created_at=request.as_of,
        )


# --------------------------------------------------------------------- pure


def _catalysts_in_universe(
    catalysts: list[Catalyst], universe: tuple[InstrumentRef, ...]
) -> dict[str, Catalyst]:
    """One catalyst per symbol, keeping the most credible when two compete."""
    tradeable = {item.symbol for item in universe}
    best: dict[str, Catalyst] = {}
    for catalyst in catalysts:
        if catalyst.symbol not in tradeable:
            continue
        running = best.get(catalyst.symbol)
        if running is None or catalyst.credibility.value > running.credibility.value:
            best[catalyst.symbol] = catalyst
    return best


def _sessions_since(catalyst: Catalyst, request: CycleRequest) -> int:
    return sessions_between(
        to_ist(catalyst.article.published_at).date(),
        to_ist(request.as_of).date(),
        request.holidays,
    )


def _iv_rank_for(symbol: str, request: CycleRequest) -> IvRank:
    """The caller's rank, or an explicit absence - never a guessed number."""
    found = request.iv_ranks.get(symbol)
    if found is not None:
        return found
    return IvRank(
        value=None,
        source=IvSource.PROXY,
        sample_count=0,
        window=0,
        method=RankMethod.PERCENTILE,
        reason=f"no IV rank supplied for {symbol}",
    )


def _reward_to_risk(inputs: SymbolInputs) -> float:
    """Max gain over max loss for the chosen structure, or 0 without one."""
    spread = inputs.options.spread
    if spread is None or spread.max_loss.paise == 0:
        return 0.0
    return spread.max_gain.paise / spread.max_loss.paise


def _rejection_from(report: GateReport) -> RejectedCandidate:
    blocker = report.first_blocker()
    if blocker is None:  # pragma: no cover - a report with no blocker is approved
        raise ValueError(f"{report.instrument_symbol}: not approved but nothing blocked it")
    return RejectedCandidate(
        instrument_symbol=report.instrument_symbol,
        killed_by=blocker.name,
        measured=blocker.measured,
        threshold=blocker.threshold,
    )


def _overflow_rejections(overflow: list[Candidate]) -> list[RejectedCandidate]:
    """Passed every gate, lost on rank. Shown, because it is a different thing
    from being killed and reads very differently to someone deciding."""
    return [
        RejectedCandidate(
            instrument_symbol=candidate.symbol,
            killed_by="rank",
            measured=f"credibility {candidate.credibility:.2f}",
            threshold="at most 2 proposals per cycle (FR-4.8)",
        )
        for candidate in overflow
    ]


def _no_ideas_reason(report: PipelineReport, rejected: list[RejectedCandidate]) -> str:
    """Why today was quiet. An empty list with no reason looks like a crash."""
    if not report.catalysts:
        return (
            f"no tradable catalyst: {report.articles_in} articles, "
            f"{report.stories_after_dedup} distinct stories, 0 survived classification"
        )
    killers: dict[str, int] = {}
    for item in rejected:
        killers[item.killed_by] = killers.get(item.killed_by, 0) + 1
    ordered = sorted(killers.items(), key=lambda pair: (-pair[1], pair[0]))
    named = ", ".join(f"{name} ({count})" for name, count in ordered)
    return f"{len(rejected)} candidate(s), all rejected: {named}"


def _risk_per_lot(
    proposals: list[TradeProposal], inputs: dict[str, SymbolInputs]
) -> dict[str, Money]:
    """Max loss per unit times the lot size, for each proposed idea."""
    risks: dict[str, Money] = {}
    for proposal in proposals:
        symbol_inputs = inputs[proposal.instrument_symbol]
        spread = symbol_inputs.options.spread
        if spread is None:  # pragma: no cover - a proposal always has a structure
            continue
        lot_size = symbol_inputs.instrument.lot_size
        risks[proposal.instrument_symbol] = Money(paise=spread.max_loss.paise * lot_size)
    return risks
