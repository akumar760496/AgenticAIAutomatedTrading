"""Export the journal to Parquet so analysis never runs against the live DB.

Three reasons this exists rather than "just query Postgres":

1. **An analysis query can lock or slow the database the scheduler depends on.**
   A briefing missed because somebody was running a GROUP BY is a bad trade.
2. **A Parquet file is a snapshot you can keep.** Re-running last month's
   calibration against today's rows answers a different question than the one
   you asked then, and silently.
3. **Nothing in the analysis path can write.** The export is read-only by
   construction, which is a stronger guarantee than remembering not to.

Files are written per table, named with the export instant, and never
overwritten - the same append-only discipline as the tables themselves.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import polars as pl
from sqlalchemy import Engine, text

from trading_desk.ports.clock import Clock

# Every journal table. Listed explicitly rather than discovered from the
# database, so a new table is an intentional addition here and not a surprise
# in the export.
JOURNAL_TABLES = (
    "briefing_ideas",
    "user_decisions",
    "outcomes",
    "override_log",
    "calibration_snapshots",
)

DEFAULT_EXPORT_DIR = Path("data/journal_exports")


@dataclass(frozen=True)
class ExportedTable:
    name: str
    path: Path
    rows: int


@dataclass(frozen=True)
class ExportReport:
    tables: list[ExportedTable]

    @property
    def total_rows(self) -> int:
        return sum(table.rows for table in self.tables)

    def summary(self) -> str:
        parts = ", ".join(f"{table.name}={table.rows}" for table in self.tables)
        return f"exported {self.total_rows} rows ({parts})"


class JournalExport:
    """Reads each journal table and writes it to a Parquet file."""

    def __init__(self, engine: Engine, clock: Clock, export_dir: Path = DEFAULT_EXPORT_DIR) -> None:
        self._engine = engine
        self._clock = clock
        self._export_dir = export_dir

    def run(self) -> ExportReport:
        stamp = self._clock.utc_now().strftime("%Y%m%dT%H%M%SZ")
        target = self._export_dir / stamp
        target.mkdir(parents=True, exist_ok=True)

        exported = []
        for table in JOURNAL_TABLES:
            frame = self._read_table(table)
            path = target / f"{table}.parquet"
            frame.write_parquet(path)
            exported.append(ExportedTable(name=table, path=path, rows=frame.height))
        return ExportReport(tables=exported)

    def _read_table(self, table: str) -> pl.DataFrame:
        """One table, whole. JSONB and UUID columns come back as strings.

        Polars has no native type for either, and a Parquet file that reads
        back as a string everywhere is easier to work with than one that fails
        to write at all. The cast is in SQL so the conversion is the database's,
        not a guess here.
        """
        with self._engine.connect() as connection:
            rows = connection.execute(text(f"SELECT * FROM {table}")).mappings().all()

        if not rows:
            return pl.DataFrame()
        return pl.DataFrame([{key: _as_cell(value) for key, value in row.items()} for row in rows])


def _as_cell(value: object) -> object:
    """Flatten what Polars cannot hold natively, leave everything else alone."""
    if isinstance(value, dict | list):
        return str(value)
    # UUID, Decimal and anything else exotic become strings; int, float, bool,
    # str, datetime and None pass through as themselves.
    if isinstance(value, int | float | bool | str | datetime) or value is None:
        return value
    # UUID, Decimal and anything else exotic.
    return str(value)
