"""Building the briefing from a finished cycle, and delivering it.

The cycle produces a `ResearchCycleResult`; this turns it into the five
sections of `docs/06` §3 and hands them to whatever is delivering today.

**The hypothetical size is the part worth reading carefully.** FR-6.2 wants it
in every briefing, and it needs an equity figure that `docs/07` does not have
(ISS-022). So when `config/risk.yaml` leaves `equity_rupees` blank, the size
line says so - every idea, every morning - rather than quietly disappearing or,
far worse, defaulting to a plausible account size. The line staying visible is
the point: an absent number that is mentioned daily gets filled in, and one
that is silently skipped does not.

Sizing goes through `domain/risk/sizing.position_size` rather than dividing
here. There must be exactly one implementation of how large a position is, and
it is the one the risk gate uses.
"""

from __future__ import annotations

from uuid import UUID

from trading_desk.application.research_cycle import CycleOutput
from trading_desk.domain.briefing import Briefing, EvidenceLine, IdeaCard, RejectedLine
from trading_desk.domain.proposals import TradeProposal
from trading_desk.domain.risk.sizing import position_size
from trading_desk.domain.timezones import to_ist
from trading_desk.domain.values import Money
from trading_desk.domain.vol import IvRank
from trading_desk.infra.risk_config import RiskConfig
from trading_desk.ports.journal import JournalPort
from trading_desk.ports.notifier import DeliveryReceipt, NotifierPort

# A lot is the unit the briefing talks in, so `position_size` is called with a
# lot size of one and a per-lot max loss. It is the same arithmetic the risk
# gate does; what changes is only what "one unit" means.
ONE_LOT = 1


class BriefingBuilder:
    def __init__(self, risk: RiskConfig, journal: JournalPort) -> None:
        self._risk = risk
        self._journal = journal

    def build(
        self,
        output: CycleOutput,
        *,
        index_iv_rank: IvRank | None = None,
        events_today: tuple[str, ...] = (),
    ) -> Briefing:
        result = output.result
        generated = to_ist(result.generated_at)

        return Briefing(
            cycle_id=result.cycle_id,
            generated_at_ist=generated.strftime("%a %d %b %Y %H:%M IST"),
            market_headline=self._headline(output, index_iv_rank),
            market_view=self._market_view(output, index_iv_rank, events_today),
            ideas=tuple(self._card(proposal, output) for proposal in result.ranked()),
            rejected=tuple(
                RejectedLine(
                    symbol=item.instrument_symbol,
                    killed_by=item.killed_by,
                    measured=item.measured,
                    threshold=item.threshold,
                )
                for item in result.rejected
            ),
            open_theses=self._open_theses(output),
            cost_lines=tuple(output.costs.as_lines()),
            no_ideas_reason=result.no_ideas_reason,
            missing_inputs=output.outcome.missing_inputs if output.outcome else (),
        )

    def _headline(self, output: CycleOutput, index_iv_rank: IvRank | None) -> str:
        """One line, and it has to be the one that changes a decision."""
        rank = (
            "IV rank unavailable" if index_iv_rank is None else f"index {index_iv_rank.describe()}"
        )
        if output.outcome is None:
            return f"cycle did not run - {rank}"
        news = output.outcome.news
        return (
            f"{news.articles_in} items, {len(news.catalysts)} catalysts, "
            f"{len(output.result.proposals)} ideas - {rank}"
        )

    def _market_view(
        self,
        output: CycleOutput,
        index_iv_rank: IvRank | None,
        events_today: tuple[str, ...],
    ) -> tuple[str, ...]:
        lines = [
            f"index IV rank: {index_iv_rank.describe() if index_iv_rank else 'unavailable'}",
            f"events today: {', '.join(events_today) if events_today else 'none listed'}",
            "term structure: not computed in v1",
            "breadth: not computed in v1",
        ]
        if output.outcome is not None:
            lines.extend(output.outcome.news.as_lines())
        return tuple(lines)

    def _card(self, proposal: TradeProposal, output: CycleOutput) -> IdeaCard:
        risk_per_lot = (
            output.outcome.risk_per_lot.get(proposal.instrument_symbol, Money.zero())
            if output.outcome
            else Money.zero()
        )
        return IdeaCard(
            idea_id=proposal.proposal_id,
            rank=proposal.rank,
            symbol=proposal.instrument_symbol,
            setup=proposal.setup_id.value,
            direction=proposal.direction.value,
            structure=_structure_of(proposal),
            expiry=proposal.legs[0].expiry.strftime("%d %b"),
            max_loss_per_lot=f"₹{risk_per_lot.rupees():,.0f}",
            reward_to_risk=proposal.gate_values.get("reward_to_risk", "see detail"),
            confidence=f"{proposal.thesis.confidence:.0%} (uncalibrated)",
            claim=proposal.thesis.claim,
            invalidation=proposal.thesis.invalidation,
            entry=proposal.entry.description,
            stop=f"₹{proposal.stop.level.rupees():,.0f} - {proposal.stop.rationale}",
            target=f"₹{proposal.target.level.rupees():,.0f} - {proposal.target.rationale}",
            hypothetical_size=self._size_line(risk_per_lot),
            gate_values=dict(proposal.gate_values),
            evidence=tuple(
                EvidenceLine(
                    claim=item.claim,
                    source=item.source_name,
                    url=item.source_ref,
                    observed_at=to_ist(item.observed_at).strftime("%d %b %H:%M IST"),
                )
                for item in proposal.thesis.evidence
            ),
            used_iv_proxy=proposal.used_iv_proxy,
        )

    def _size_line(self, risk_per_lot: Money) -> str:
        """The advisory size, or the reason there isn't one (FR-6.2, ISS-022)."""
        equity = self._risk.equity
        if equity is None:
            return f"unavailable - {self._risk.missing_reason()}"
        if risk_per_lot.paise <= 0:
            return "unavailable - the structure has no priced max loss"

        size = position_size(equity, risk_per_lot, ONE_LOT)
        if not size.is_tradable:
            return (
                f"0 lots - one lot risks ₹{risk_per_lot.rupees():,.0f} against a "
                f"budget of ₹{size.risk_budget.rupees():,.0f}"
            )
        return (
            f"{size.lots} lot(s), risking ₹{size.total_risk.rupees():,.0f} "
            f"of a ₹{size.risk_budget.rupees():,.0f} budget (advisory)"
        )

    def _open_theses(self, output: CycleOutput) -> tuple[str, ...]:
        """Ideas from earlier cycles that you have not answered yet."""
        awaiting = self._journal.ideas_awaiting_decision(output.result.generated_at)
        return tuple(
            f"{idea.symbol} {idea.direction} from "
            f"{to_ist(idea.created_at).strftime('%d %b')} - no decision recorded"
            for idea in awaiting
        )


def _structure_of(proposal: TradeProposal) -> str:
    return " / ".join(
        f"{leg.action.value} {leg.strike.rupees():g} {leg.right.value}" for leg in proposal.legs
    )


def deliver(briefing: Briefing, notifier: NotifierPort) -> list[DeliveryReceipt]:
    """Send the decision first, then ask, then send the audit trail.

    The order is the requirement, not a preference: `docs/12` asks that the
    briefing be actionable "without scrolling past the decision", and on a
    phone the first message is the only one that is reliably read before a
    decision is made.
    """
    receipts = [notifier.send_briefing(briefing.headline())]
    receipts.extend(
        notifier.ask_decision(idea.idea_id, _ask_text(idea.idea_id, briefing))
        for idea in briefing.ideas
    )
    receipts.append(notifier.send_briefing(briefing.detail()))
    return receipts


def _ask_text(idea_id: UUID, briefing: Briefing) -> str:
    idea = next(item for item in briefing.ideas if item.idea_id == idea_id)
    return f"{idea.symbol} #{idea.rank} - what did you do?"
