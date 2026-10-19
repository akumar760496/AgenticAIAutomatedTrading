"""An in-memory job log."""

from __future__ import annotations

from datetime import date

from trading_desk.ports.job_log import JobRun


class FakeJobLog:
    def __init__(self) -> None:
        self.recorded: list[JobRun] = []

    def record(self, run: JobRun) -> None:
        self.recorded.append(run)

    def runs(self, job: str, since: date) -> list[JobRun]:
        matching = [run for run in self.recorded if run.job == job and run.trading_date >= since]
        matching.sort(key=lambda run: run.at)
        return matching
