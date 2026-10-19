"""The job log on disk.

It exists to explain the mornings when something else was broken, so the
interesting tests are about reading it back after damage.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

from trading_desk.adapters.store.jsonl_job_log import JsonlJobLog
from trading_desk.ports.job_log import JobOutcome, JobRun

MONDAY = date(2026, 10, 19)


def _run(outcome: JobOutcome = JobOutcome.RAN, day: date = MONDAY) -> JobRun:
    return JobRun(
        job="brief",
        outcome=outcome,
        at=datetime(day.year, day.month, day.day, 2, 45, tzinfo=UTC),
        trading_date=day,
        detail="",
    )


class TestRoundTrip:
    def test_a_recorded_run_reads_back_identically(self, tmp_path: Path) -> None:
        log = JsonlJobLog(tmp_path / "runs.jsonl")
        log.record(_run())

        assert log.runs("brief", since=date(2026, 10, 1)) == [_run()]

    def test_another_jobs_runs_are_not_returned(self, tmp_path: Path) -> None:
        log = JsonlJobLog(tmp_path / "runs.jsonl")
        log.record(_run())

        assert log.runs("review", since=date(2026, 10, 1)) == []

    def test_runs_before_since_are_excluded(self, tmp_path: Path) -> None:
        log = JsonlJobLog(tmp_path / "runs.jsonl")
        log.record(_run(day=date(2026, 9, 1)))

        assert log.runs("brief", since=date(2026, 10, 1)) == []

    def test_a_missing_file_reads_as_no_runs(self, tmp_path: Path) -> None:
        """First ever start. Not an error."""
        log = JsonlJobLog(tmp_path / "never-written.jsonl")

        assert log.runs("brief", since=date(2026, 10, 1)) == []


class TestDamage:
    def test_a_truncated_last_line_does_not_lose_the_earlier_runs(self, tmp_path: Path) -> None:
        """A power cut mid-append. Losing one run beats refusing to read 400."""
        path = tmp_path / "runs.jsonl"
        log = JsonlJobLog(path)
        log.record(_run(day=date(2026, 10, 16)))
        log.record(_run(day=MONDAY))
        with path.open("a", encoding="utf-8") as handle:
            handle.write('{"job": "brief", "outcome": "RA')

        found = log.runs("brief", since=date(2026, 10, 1))

        assert [run.trading_date for run in found] == [date(2026, 10, 16), MONDAY]

    def test_it_only_ever_appends(self, tmp_path: Path) -> None:
        """The history is the evidence; a second record must not replace one."""
        log = JsonlJobLog(tmp_path / "runs.jsonl")
        log.record(_run(JobOutcome.FAILED))
        log.record(_run(JobOutcome.RAN))

        outcomes = [run.outcome for run in log.runs("brief", since=date(2026, 10, 1))]
        assert outcomes == [JobOutcome.FAILED, JobOutcome.RAN]
