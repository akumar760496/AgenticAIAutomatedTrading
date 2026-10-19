"""Assembling one Setup A candidate from five specialists' answers.

Pure plumbing on purpose. Every field below is copied from something a
specialist measured; nothing is computed here and nothing is defaulted to a
convenient value. Where a specialist had no answer, the `None` travels through
and the gate reports NO_DATA, which blocks (rule 6).

**Two fields have no data source at all**, and `SetupACandidate` types them as
plain booleans with permissive defaults, so they cannot carry "unknown":

- `in_ban_list` - the NSE F&O ban list is published daily and free, and we do
  not fetch it. An unfetched ban list defaults to "not banned".
- `results_date_in_window` - there is no earnings calendar, so "no results
  inside the expiry window" is what the gate sees whether or not that is true.

Both default to the *permissive* answer, which is the wrong direction for a
gate. They are passed in from the request rather than hardcoded here so that
wiring a source later is a change at the edge, and the cycle reports them as
missing inputs every run (ISS-028).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from trading_desk.agents.fundamental.agent import FundamentalView
from trading_desk.agents.news.pipeline import Catalyst
from trading_desk.agents.options.agent import OptionsView
from trading_desk.agents.portfolio.agent import PortfolioView
from trading_desk.agents.technical.agent import TechnicalView
from trading_desk.domain.catalysts import CatalystType
from trading_desk.domain.instruments import InstrumentRef
from trading_desk.domain.setups.setup_a import SetupACandidate
from trading_desk.domain.vol import IvRank, IvSource


@dataclass(frozen=True)
class SymbolInputs:
    """Everything the five specialists found about one symbol."""

    instrument: InstrumentRef
    catalyst: Catalyst
    fundamental: FundamentalView
    technical: TechnicalView
    options: OptionsView
    portfolio: PortfolioView
    iv_rank: IvRank
    sessions_since_catalyst: int
    in_ban_list: bool = False
    results_date_in_window: date | None = None


def build_setup_a_candidate(inputs: SymbolInputs) -> SetupACandidate:
    label = inputs.catalyst.classified.label
    return SetupACandidate(
        instrument_symbol=inputs.instrument.symbol,
        direction=inputs.catalyst.impact.direction,
        sessions_since_catalyst=inputs.sessions_since_catalyst,
        catalyst_type=label.catalyst_type.value,
        credibility=inputs.catalyst.credibility.value,
        move_since_catalyst=inputs.technical.move_since_catalyst,
        atr=inputs.technical.atr,
        close_beyond_range_edge=inputs.technical.close_beyond_range_edge,
        volume_ratio=inputs.technical.volume_ratio,
        trend_regime=inputs.technical.trend,
        iv_rank=inputs.iv_rank.value,
        iv_rank_is_proxy=inputs.iv_rank.source is IvSource.PROXY,
        legs=inputs.options.legs,
        days_to_expiry=inputs.options.days_to_expiry,
        results_date_in_window=inputs.results_date_in_window,
        catalyst_is_results=label.catalyst_type is CatalystType.EARNINGS_SURPRISE,
        in_ban_list=inputs.in_ban_list,
        governance_flags=inputs.fundamental.governance_flags,
    )


def facts_for_synthesis(inputs: SymbolInputs, gate_values: dict[str, str]) -> str:
    """The only text the synthesis model is allowed to draw on.

    Assembled here rather than in the writer so that what the model may say is
    decided in the same place as what the gates measured. Every number the
    model writes has to appear in this string (`domain/grounding.py`), so an
    omission here silently narrows what it can say - which is the safe
    direction to be wrong in.
    """
    catalyst = inputs.catalyst
    lines = [
        f"Instrument: {inputs.instrument.symbol}",
        f"Direction (decided by the impact analyst): {catalyst.impact.direction.value}",
        f"Catalyst type: {catalyst.classified.label.catalyst_type.value}",
        f"Catalyst headline: {catalyst.article.headline}",
        f"Published by: {catalyst.article.publisher} on "
        f"{catalyst.article.published_at.date().isoformat()}",
        f"Also reported by: {', '.join(catalyst.also_reported_by) or 'nobody else'}",
        f"Credibility: {catalyst.credibility.explain()}",
        f"Expected horizon: {catalyst.impact.horizon_days} days",
        f"Structure: {inputs.options.choice.kind.value} ({inputs.options.choice.reason})",
    ]
    lines.extend(f"Gate {name}: {value}" for name, value in sorted(gate_values.items()))
    lines.extend(
        f"Technical {name}: {value}"
        for name, value in sorted(inputs.technical.finding.measurements.items())
    )
    lines.extend(
        f"Options {name}: {value}"
        for name, value in sorted(inputs.options.finding.measurements.items())
    )
    if inputs.fundamental.finding.notes:
        lines.append("Fundamental notes: " + "; ".join(inputs.fundamental.finding.notes))
    return "\n".join(lines)
