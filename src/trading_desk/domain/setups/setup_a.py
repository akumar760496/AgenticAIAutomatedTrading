"""Setup A - Catalyst Continuation. Nine gates, each independently testable.

> A credible company-specific catalyst the market has not yet fully priced,
> confirmed by price and volume, expressed as a defined-risk debit vertical.

Three properties this module is built around:

1. **Every gate returns what it measured** (rule 9). "IV rank 61.2, needed
   under 60" is actionable; "no" is not.
2. **Nothing short-circuits.** All nine run, so the briefing can show that an
   idea failed one gate narrowly rather than six badly.
3. **Missing data blocks** (rule 6). A gate with no input returns NO_DATA,
   which is not a pass. Today that matters more than it sounds: IV rank is
   unavailable for most of the universe, so this gate stops most candidates
   before any judgement is made - and says so.

Order matters only for which blocker gets named first in a rejection, so the
cheapest and most discriminating come first. docs/06 is explicit that
`not_already_priced` kills most ideas, and that this is correct rather than a
tuning problem.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from trading_desk.domain.enums import Direction, TrendRegime
from trading_desk.domain.gates import GateReport, GateResult, failed, no_data, passed
from trading_desk.domain.setups.thresholds import SetupAThresholds
from trading_desk.domain.values import Money


@dataclass(frozen=True)
class LegLiquidity:
    """What is known about one option leg's tradability."""

    open_interest_lots: int
    bid: Money
    ask: Money

    def mid(self) -> Money:
        return Money(paise=(self.bid.paise + self.ask.paise) // 2)

    def spread_fraction(self) -> float | None:
        """Bid-ask as a share of mid, or None when there is no mid to divide by.

        None rather than 1.0 or infinity: a leg quoted 0/0 is untradable *and*
        unmeasurable, and the gate should say the second thing.
        """
        mid = self.mid()
        if mid.paise <= 0:
            return None
        return (self.ask.paise - self.bid.paise) / mid.paise


@dataclass(frozen=True)
class SetupACandidate:
    """Everything the nine gates need, already gathered.

    A plain value object on purpose: the agent's job is to fill this in, the
    gate's job is to judge it. An agent that reasons about thresholds instead
    of calling these functions is the failure mode step 3.3 is written to
    avoid.
    """

    instrument_symbol: str
    direction: Direction
    # None where the input could not be gathered - each becomes NO_DATA, never
    # an assumed pass.
    sessions_since_catalyst: int | None = None
    catalyst_type: str | None = None
    credibility: float | None = None
    move_since_catalyst: Money | None = None
    atr: Money | None = None
    close_beyond_range_edge: bool | None = None
    volume_ratio: float | None = None
    trend_regime: TrendRegime | None = None
    iv_rank: float | None = None
    iv_rank_is_proxy: bool = False
    legs: tuple[LegLiquidity, ...] = ()
    days_to_expiry: int | None = None
    results_date_in_window: date | None = None
    catalyst_is_results: bool = False
    in_ban_list: bool = False
    governance_flags: tuple[str, ...] = ()


def catalyst_recency(candidate: SetupACandidate, limits: SetupAThresholds) -> GateResult:
    """Within the last N *trading sessions* - a Friday filing is fresh Monday."""
    name = "catalyst_recency"
    threshold = f"<= {limits.max_sessions_since_catalyst} sessions"
    if candidate.sessions_since_catalyst is None:
        return no_data(name, threshold, "no catalyst identified")

    measured = f"{candidate.sessions_since_catalyst} sessions"
    if candidate.sessions_since_catalyst <= limits.max_sessions_since_catalyst:
        return passed(name, measured, threshold)
    return failed(name, measured, threshold, "the catalyst is no longer news")


def catalyst_type_allowed(candidate: SetupACandidate, limits: SetupAThresholds) -> GateResult:
    """A closed list. An LLM will call anything a catalyst if allowed to."""
    name = "catalyst_type"
    threshold = f"one of {sorted(limits.catalyst_types)}"
    if not candidate.catalyst_type:
        return no_data(name, threshold, "no catalyst type classified")

    measured = candidate.catalyst_type.upper()
    if measured in limits.catalyst_types:
        return passed(name, measured, threshold)
    return failed(name, measured, threshold, "not a tradable catalyst type")


def credibility(candidate: SetupACandidate, limits: SetupAThresholds) -> GateResult:
    name = "credibility"
    threshold = f">= {limits.min_credibility}"
    if candidate.credibility is None:
        return no_data(name, threshold, "no credibility score")

    measured = f"{candidate.credibility:.2f}"
    if candidate.credibility >= limits.min_credibility:
        return passed(name, measured, threshold)
    return failed(name, measured, threshold, "'sources say' stories cannot trigger alone")


def not_already_priced(candidate: SetupACandidate, limits: SetupAThresholds) -> GateResult:
    """Move since the catalyst, in ATR units. The gate that kills most ideas.

    docs/06 says so explicitly and calls it correct: if the move has already
    happened, the edge is gone. A low pass rate here is the setup working.
    """
    name = "not_already_priced"
    threshold = f"< {limits.max_move_in_atr} x ATR"
    if candidate.move_since_catalyst is None or candidate.atr is None:
        return no_data(name, threshold, "need both the move and an ATR")
    if candidate.atr.paise <= 0:
        return no_data(name, threshold, "ATR is zero - cannot express the move in ATR units")

    multiple = abs(candidate.move_since_catalyst.paise) / candidate.atr.paise
    measured = f"{multiple:.2f} x ATR"
    if multiple < limits.max_move_in_atr:
        return passed(name, measured, threshold)
    return failed(name, measured, threshold, "the catalyst is already in the price")


def technical_confirm(candidate: SetupACandidate, limits: SetupAThresholds) -> GateResult:
    """Close beyond the 20-day range edge, on volume. Both, not either."""
    name = "technical_confirm"
    threshold = f"beyond the 20d range edge AND volume >= {limits.min_volume_ratio}x"
    if candidate.close_beyond_range_edge is None or candidate.volume_ratio is None:
        return no_data(name, threshold, "need both the range break and a volume ratio")

    measured = f"break={candidate.close_beyond_range_edge}, volume={candidate.volume_ratio:.2f}x"
    if candidate.close_beyond_range_edge and candidate.volume_ratio >= limits.min_volume_ratio:
        return passed(name, measured, threshold)
    return failed(name, measured, threshold, "price and volume must both confirm")


def volatility_not_elevated(candidate: SetupACandidate, limits: SetupAThresholds) -> GateResult:
    """Never buy premium into an already-panicked chain.

    The proxy flag rides along in the detail rather than changing the verdict:
    a proxy-derived rank is still the best available answer, and hiding that it
    was a proxy is what the journal's `used_iv_proxy` column exists to prevent.
    """
    name = "iv_rank"
    threshold = f"< {limits.max_iv_rank}"
    if candidate.iv_rank is None:
        return no_data(name, threshold, "no IV rank - chain history is still accruing")

    source = "proxy" if candidate.iv_rank_is_proxy else "actual"
    measured = f"{candidate.iv_rank:.1f} ({source})"
    if candidate.iv_rank < limits.max_iv_rank:
        return passed(name, measured, threshold)
    return failed(name, measured, threshold, "premium is already expensive")


def liquidity(candidate: SetupACandidate, limits: SetupAThresholds) -> GateResult:
    """Both legs must clear. One tradable leg and one untradable is untradable."""
    name = "liquidity"
    threshold = (
        f"every leg OI >= {limits.liquidity.min_open_interest_lots} lots and "
        f"spread <= {limits.liquidity.max_spread_fraction:.0%} of mid"
    )
    if not candidate.legs:
        return no_data(name, threshold, "no legs priced")

    problems = []
    for index, leg in enumerate(candidate.legs):
        if leg.open_interest_lots < limits.liquidity.min_open_interest_lots:
            problems.append(f"leg {index} OI {leg.open_interest_lots}")
        fraction = leg.spread_fraction()
        if fraction is None:
            return no_data(name, threshold, f"leg {index} has no mid to measure a spread against")
        if fraction > limits.liquidity.max_spread_fraction:
            problems.append(f"leg {index} spread {fraction:.1%}")

    measured = ", ".join(
        f"leg {index}: OI {leg.open_interest_lots}, spread {(leg.spread_fraction() or 0):.1%}"
        for index, leg in enumerate(candidate.legs)
    )
    if problems:
        return failed(name, measured, threshold, "; ".join(problems))
    return passed(name, measured, threshold)


def calendar(candidate: SetupACandidate, limits: SetupAThresholds) -> GateResult:
    """Expiry window, results inside it, and the ban list.

    Results inside the window are disqualifying **unless results are the
    catalyst** - holding a directional debit spread through an unrelated print
    is a different trade from the one that was researched.
    """
    name = "calendar"
    threshold = (
        f"{limits.expiry.min_days}-{limits.expiry.max_days} DTE, no unrelated results "
        "inside, not banned"
    )
    if candidate.days_to_expiry is None:
        return no_data(name, threshold, "no expiry selected")

    measured = f"{candidate.days_to_expiry} DTE"
    if candidate.in_ban_list:
        return failed(name, measured, threshold, "on the NSE F&O ban list today")
    if not limits.expiry.min_days <= candidate.days_to_expiry <= limits.expiry.max_days:
        return failed(name, measured, threshold, "outside the expiry window")
    if candidate.results_date_in_window is not None and not candidate.catalyst_is_results:
        return failed(
            name,
            f"{measured}, results {candidate.results_date_in_window}",
            threshold,
            "unrelated results fall inside the window",
        )
    return passed(name, measured, threshold)


def governance_clear(candidate: SetupACandidate, limits: SetupAThresholds) -> GateResult:
    """A veto, not a score. One open flag is enough."""
    del limits  # the veto has no threshold to tune, and that is deliberate
    name = "governance_veto"
    threshold = "no open governance flag"
    if candidate.governance_flags:
        return failed(
            name,
            ", ".join(candidate.governance_flags),
            threshold,
            "auditor exit, pledge spike or promoter sell-down is disqualifying",
        )
    return passed(name, "none", threshold)


# Cheapest and most discriminating first, so `first_blocker` names the most
# informative gate in a rejection.
GATES = (
    catalyst_recency,
    catalyst_type_allowed,
    credibility,
    not_already_priced,
    technical_confirm,
    volatility_not_elevated,
    liquidity,
    calendar,
    governance_clear,
)


def evaluate(candidate: SetupACandidate, limits: SetupAThresholds) -> GateReport:
    """Run every gate. Never short-circuits - see the module docstring."""
    return GateReport(
        instrument_symbol=candidate.instrument_symbol,
        results=tuple(gate(candidate, limits) for gate in GATES),
    )
