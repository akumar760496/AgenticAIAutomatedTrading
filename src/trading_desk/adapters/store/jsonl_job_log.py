"""The job log as one appended JSONL file.

A file rather than a table, for one reason: this log has to survive the
database being down. "Postgres was unreachable at 08:15" is exactly the kind of
morning the log exists to explain, and a log that lives in Postgres cannot
explain it.

Append-only by construction - the file is only ever opened in "a" mode - and
the whole file is read back, because 250 trading days of a dozen jobs is a few
thousand short lines.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

from trading_desk.ports.job_log import JobOutcome, JobRun

DEFAULT_JOB_LOG_PATH = Path("data/job_runs.jsonl")


class JsonlJobLog:
    def __init__(self, path: Path = DEFAULT_JOB_LOG_PATH) -> None:
        self._path = path

    def record(self, run: JobRun) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(
            {
                "job": run.job,
                "outcome": str(run.outcome),
                "at": run.at.isoformat(),
                "trading_date": run.trading_date.isoformat(),
                "detail": run.detail,
            }
        )
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    def runs(self, job: str, since: date) -> list[JobRun]:
        if not self._path.is_file():
            return []

        found: list[JobRun] = []
        for line in self._path.read_text(encoding="utf-8").splitlines():
            parsed = _parse(line)
            if parsed is None:
                continue
            if parsed.job == job and parsed.trading_date >= since:
                found.append(parsed)

        found.sort(key=lambda run: run.at)
        return found


def _parse(line: str) -> JobRun | None:
    """One line, or None if it is not readable.

    A truncated last line is the expected damage from a power cut mid-append,
    and losing one run beats refusing to read the other four hundred.
    """
    text = line.strip()
    if not text:
        return None
    try:
        raw = json.loads(text)
        return JobRun(
            job=str(raw["job"]),
            outcome=JobOutcome(raw["outcome"]),
            at=datetime.fromisoformat(raw["at"]),
            trading_date=date.fromisoformat(raw["trading_date"]),
            detail=str(raw.get("detail", "")),
        )
    except (json.JSONDecodeError, KeyError, ValueError):
        return None
