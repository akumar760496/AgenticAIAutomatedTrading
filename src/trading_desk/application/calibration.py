"""Reading the journal's scored outcomes and asking whether confidence meant anything.

Three things come out of one run: a `Calibration` (Brier, its decomposition and
the reliability curve), a `SizingLock` state, and a report you can read. The
snapshot is recorded in `calibration_snapshots`, which is append-only, so the
history of *being wrong* survives - a tracker that only ever shows its current
number cannot show you that it improved.

**Proxy-derived ideas are reported separately, not mixed in.** Until roughly six
months of chain history accrues, most ideas' IV rank comes from the realised-vol
proxy (ISS-002, step 1.8). Those ideas went through a different volatility gate
from the ones that follow, so averaging them together measures two systems and
reports one number. The flag has been recorded on every idea since 1.8 for
exactly this moment.

**The scope is `ALL` plus one row per setup.** Setup A and Setup B are different
strategies with different base rates, and a combined Brier score can hide one
being useless while the other carries it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from trading_desk.domain.calibration import Calibration, ResolvedIdea, calibrate, succeeded
from trading_desk.domain.sizing_lock import SizingLock, sizing_lock
from trading_desk.domain.timezones import to_ist
from trading_desk.ports.clock import Clock
from trading_desk.ports.journal import CalibrationSnapshot, JournalPort, ResolvedScore
from trading_desk.ports.notifier import DeliveryReceipt, NotifierPort

# How many resolved outcomes one run reads. Generous on purpose: the window is
# the whole history until there is more history than this.
MAX_RESOLVED = 1000

ALL_SCOPE = "ALL"


@dataclass(frozen=True)
class ScopedCalibration:
    """One slice's calibration, with the slice named."""

    scope: str
    calibration: Calibration

    def as_line(self) -> str:
        return f"{self.scope}: {self.calibration.summary()}"


@dataclass(frozen=True)
class CalibrationReport:
    """What one run measured, and whether sizing may change."""

    as_of: datetime
    scopes: tuple[ScopedCalibration, ...]
    lock: SizingLock
    proxy_count: int
    real_iv_count: int

    @property
    def overall(self) -> Calibration | None:
        for scoped in self.scopes:
            if scoped.scope == ALL_SCOPE:
                return scoped.calibration
        return None

    def text(self) -> str:
        when = to_ist(self.as_of)
        lines = [f"CALIBRATION {when:%a %d %b %Y} IST", ""]

        if not self.scopes:
            lines.append(
                "No resolved outcomes yet, so there is nothing to calibrate. "
                "This is the expected state until ideas start resolving."
            )
            lines.append("")
            lines.append(f"Sizing lock: {self.lock.describe()}")
            return "\n".join(lines)

        lines.extend(scoped.as_line() for scoped in self.scopes)
        lines.append("")
        lines.append(f"Sizing lock: {self.lock.describe()}")

        if self.proxy_count:
            lines.append("")
            lines.append(
                f"{self.proxy_count} of {self.proxy_count + self.real_iv_count} resolved "
                f"ideas had a proxy-derived IV rank (ISS-002). They went through a "
                f"different volatility gate from the rest, so treat a mixed number "
                f"as provisional."
            )

        overall = self.overall
        if overall is not None:
            lines.append("")
            lines.append("Reliability curve (claimed vs observed):")
            lines.extend(f"  {band.describe()}" for band in overall.bins if band.count)
        return "\n".join(lines)


class CalibrationCycle:
    """Computes calibration over the journal's scored outcomes and records it."""

    def __init__(
        self,
        *,
        journal: JournalPort,
        clock: Clock,
        limit: int = MAX_RESOLVED,
    ) -> None:
        self._journal = journal
        self._clock = clock
        self._limit = limit

    def run(self) -> CalibrationReport:
        now = self._clock.utc_now()
        scores = self._journal.resolved_scores(self._limit)

        if not scores:
            # No calibration, not a calibration of zero. `sizing_lock(None)`
            # keeps the lock shut and says which of the two reasons applies.
            return CalibrationReport(
                as_of=now,
                scopes=(),
                lock=sizing_lock(None),
                proxy_count=0,
                real_iv_count=0,
            )

        scopes = [ScopedCalibration(ALL_SCOPE, calibrate(_as_ideas(scores)))]
        for setup_id in sorted({item.setup_id for item in scores}):
            slice_ = [item for item in scores if item.setup_id == setup_id]
            scopes.append(ScopedCalibration(setup_id, calibrate(_as_ideas(slice_))))

        for scoped in scopes:
            self._journal.record_calibration(
                CalibrationSnapshot(
                    as_of=now,
                    scope=scoped.scope,
                    brier_score=scoped.calibration.brier,
                    baseline_brier_score=scoped.calibration.baseline_brier,
                    sample_count=scoped.calibration.sample_count,
                    reliability_bins=scoped.calibration.as_stored_bins(),
                )
            )

        return CalibrationReport(
            as_of=now,
            scopes=tuple(scopes),
            # The lock reads the ALL scope. A single setup beating the baseline
            # over fifty of its own ideas is a different and later argument.
            lock=sizing_lock(scopes[0].calibration),
            proxy_count=sum(1 for item in scores if item.used_iv_proxy),
            real_iv_count=sum(1 for item in scores if not item.used_iv_proxy),
        )


def _as_ideas(scores: list[ResolvedScore]) -> tuple[ResolvedIdea, ...]:
    """Reduce to the two numbers calibration needs. Success is net money."""
    return tuple(
        ResolvedIdea(confidence=item.confidence, succeeded=succeeded(item.net_pnl.paise))
        for item in scores
    )


def deliver(report: CalibrationReport, notifier: NotifierPort) -> DeliveryReceipt:
    """Send the report. Raises if it could not be delivered (rule 6)."""
    return notifier.send_briefing(report.text())
