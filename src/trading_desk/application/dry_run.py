"""Run the gates over history and report what would have fired.

**The point-in-time guard is the reason this module exists in `application/`
rather than as a notebook.** A backtest that can see data published after the
decision moment produces beautiful, worthless results, and the failure is
silent - the numbers simply come out better. `PointInTimeView` makes the
lookahead impossible to express: it filters by timestamp on the way out, so a
caller cannot accidentally read tomorrow's bar.

What this is honest about:

- **History is shallow.** Daily bars go back to 2023 for two names; the chain
  archive is three days old. A dry run today measures the plumbing, not the
  edge. docs/10 says "shallow at first - that's expected".
- **It reports what the gates said, not what the trade earned.** Outcome
  scoring needs exits, which need intraday data the archive does not have yet.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from trading_desk.domain.gates import GateReport
from trading_desk.domain.market import Bar
from trading_desk.domain.setups.setup_a import SetupACandidate, evaluate
from trading_desk.domain.setups.thresholds import SetupAThresholds


class LookaheadError(RuntimeError):
    """Something asked for data published after the decision moment."""


@dataclass(frozen=True)
class PointInTimeView:
    """Bars, filtered to what was knowable at `as_of`.

    The filter is applied on read rather than trusted to the caller, because
    "remember not to look ahead" is exactly the instruction that gets
    forgotten in a loop body at the end of a long function.
    """

    bars: tuple[Bar, ...]
    as_of: datetime

    def __post_init__(self) -> None:
        if self.as_of.tzinfo is None:
            raise ValueError("as_of must be tz-aware")

    def visible(self) -> tuple[Bar, ...]:
        """Only bars whose session had already closed at `as_of`."""
        return tuple(bar for bar in self.bars if bar.start <= self.as_of)

    def require_no_future(self) -> None:
        """Raise if any bar in the window postdates the decision moment.

        Used by the lookahead test, which injects a future bar and expects
        this to notice. A guard that is never driven to fire is decoration.
        """
        future = [bar for bar in self.bars if bar.start > self.as_of]
        if future:
            raise LookaheadError(
                f"{len(future)} bar(s) postdate the decision moment {self.as_of.isoformat()}; "
                f"earliest offender {future[0].start.isoformat()}"
            )


@dataclass
class DryRunReport:
    """What the gates said over the available history."""

    days_evaluated: int = 0
    candidates_evaluated: int = 0
    would_have_fired: list[tuple[date, str]] = field(default_factory=list)
    blocked_by: dict[str, int] = field(default_factory=dict)

    @property
    def fire_rate(self) -> float:
        if not self.candidates_evaluated:
            return 0.0
        return len(self.would_have_fired) / self.candidates_evaluated

    def summary(self) -> str:
        if not self.candidates_evaluated:
            return "no candidates evaluated - not enough history"
        top = sorted(self.blocked_by.items(), key=lambda pair: -pair[1])[:3]
        killers = ", ".join(f"{name} x{count}" for name, count in top)
        return (
            f"{self.candidates_evaluated} candidates over {self.days_evaluated} day(s): "
            f"{len(self.would_have_fired)} would have fired ({self.fire_rate:.1%}); "
            f"top blockers {killers}"
        )


class DryRun:
    """Replays candidates through the Setup A gates, point-in-time."""

    def __init__(self, limits: SetupAThresholds) -> None:
        self._limits = limits

    def evaluate_candidates(self, candidates: list[tuple[date, SetupACandidate]]) -> DryRunReport:
        report = DryRunReport(days_evaluated=len({day for day, _ in candidates}))

        for day, candidate in candidates:
            result: GateReport = evaluate(candidate, self._limits)
            report.candidates_evaluated += 1
            if result.approved:
                report.would_have_fired.append((day, candidate.instrument_symbol))
                continue
            for blocker in result.blockers():
                report.blocked_by[blocker.name] = report.blocked_by.get(blocker.name, 0) + 1

        return report
