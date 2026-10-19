"""The system watching its own behaviour: did it brief too much, or go quiet?

Two numbers from `domain/monitors.py`, with the data gathered here:

- **The no-trade rate** needs two facts that live in two places. How many
  sessions the briefing job actually *ran* comes from the job log (step 3.7) -
  not from the calendar, because a session the scheduler missed is not a
  session the gates were quiet on. How many of those produced an idea comes
  from the journal. A day where the job ran and no idea was recorded is a
  genuine no-trade session.

  **Using trading days instead of job runs would be the subtle version of this
  bug**: three days when the box was off would read as three quiet sessions,
  and a dead feed would look like a disciplined system.

- **Engagement** is briefed ideas against ideas with any decision.

The alert is CRITICAL when the rate leaves the band, because both directions
mean the briefing cannot be trusted: too loose and it is approving everything,
too quiet and something upstream is dead. It is sent once per run and the run
is a daily job, so it will nag until the band is back - which is the intent.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from trading_desk.application.scheduler import BRIEF
from trading_desk.domain.monitors import Engagement, NoTradeRate
from trading_desk.domain.timezones import to_ist
from trading_desk.ports.clock import Clock
from trading_desk.ports.job_log import JobLogPort, JobOutcome
from trading_desk.ports.journal import JournalPort
from trading_desk.ports.notifier import AlertLevel, NotifierPort

log = logging.getLogger(__name__)

# The window both numbers are measured over. Thirty sessions is about six weeks
# and is long enough for the 40-60% band to mean something without reaching so
# far back that a threshold change gets averaged away.
DEFAULT_WINDOW_DAYS = 45


@dataclass(frozen=True)
class HealthReport:
    """Both monitors, and whether either needs attention."""

    as_of: datetime
    window_days: int
    no_trade: NoTradeRate
    engagement: Engagement

    @property
    def needs_alert(self) -> bool:
        return self.no_trade.needs_alert

    def text(self) -> str:
        when = to_ist(self.as_of)
        return "\n".join(
            [
                f"HEALTH {when:%a %d %b %Y} IST, last {self.window_days} days",
                "",
                self.no_trade.describe(),
                self.engagement.describe(),
            ]
        )


class HealthMonitor:
    """Measures the no-trade rate and engagement, and alerts outside the band."""

    def __init__(
        self,
        *,
        journal: JournalPort,
        job_log: JobLogPort,
        clock: Clock,
        notifier: NotifierPort,
        window_days: int = DEFAULT_WINDOW_DAYS,
    ) -> None:
        self._journal = journal
        self._job_log = job_log
        self._clock = clock
        self._notifier = notifier
        self._window = window_days

    def run(self) -> HealthReport:
        now = self._clock.utc_now()
        since = to_ist(now).date() - timedelta(days=self._window)

        sessions = self._sessions_briefed(since)
        with_ideas = {day for day in self._journal.briefed_dates(since) if day in sessions}
        briefed, answered = self._journal.engagement_counts(since)

        report = HealthReport(
            as_of=now,
            window_days=self._window,
            no_trade=NoTradeRate(
                sessions=len(sessions),
                sessions_with_no_idea=len(sessions) - len(with_ideas),
            ),
            engagement=Engagement(briefings_sent=briefed, briefings_answered=answered),
        )

        if report.needs_alert:
            self._notifier.send_alert(AlertLevel.CRITICAL, report.no_trade.describe())
        log.info("health: %s | %s", report.no_trade.describe(), report.engagement.describe())
        return report

    def _sessions_briefed(self, since: date) -> set[date]:
        """IST dates the briefing job actually ran on.

        `RAN` only. A `SKIPPED` run is a holiday and a `FAILED` run produced no
        briefing at all - counting either as a session would put a day in the
        denominator that the gates never saw.
        """
        return {
            run.trading_date
            for run in self._job_log.runs(BRIEF, since)
            if run.outcome is JobOutcome.RAN
        }
