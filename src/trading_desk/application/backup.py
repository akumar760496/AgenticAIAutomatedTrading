"""Take a backup, and prove a restore works.

Two services, and the second is the one that matters. A backup job that has
never been restored from is not a backup - it is an untested assumption with a
cron entry, and the moment you find out is the worst possible moment.

What is protected and why it is not replaceable:

- **The option-chain archive.** Three captures a day of the IV surface. Nobody
  sells this history cheaply and it cannot be re-fetched; a lost month is a
  lost month.
- **The journal.** Every idea, decision and outcome. Backfilling it would mean
  reconstructing what was believed at the time, which is the one thing that
  cannot be reconstructed.
- Bars, filings and articles are re-fetchable in principle, slowly.

**Object storage is deliberately not wired up here.** `docs/10` asks for a
versioned bucket, which needs a provider, an account and credentials - a
purchase decision for the user, not something to assume. Backups are written
locally and `BackupReport.destination` is the path a sync job would upload.
Until that exists, a backup on the same laptop as the database protects against
a dropped table, not a dropped laptop, and `verify 1_9` says so.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from trading_desk.domain.backup import (
    DEFAULT_MAX_BACKUP_AGE,
    CountMismatch,
    Staleness,
    count_mismatches,
    refuse_unsafe_restore,
    scratch_name,
    staleness,
)
from trading_desk.ports.backup import BackupToolPort
from trading_desk.ports.clock import Clock
from trading_desk.ports.notifier import AlertLevel, NotifierPort

DEFAULT_BACKUP_DIR = Path("data/backups")
DUMP_NAME = "postgres.dump"
MANIFEST_NAME = "chain_archive.sha256"

# Read in chunks so a large Parquet file does not land in memory whole.
HASH_CHUNK_BYTES = 1 << 20


@dataclass
class BackupReport:
    taken_at: datetime
    destination: Path
    dump_bytes: int
    archive_files: int
    archive_digest: str

    def summary(self) -> str:
        return (
            f"dump {self.dump_bytes / 1_048_576:.1f} MB, "
            f"{self.archive_files} archive files, digest {self.archive_digest[:12]}"
        )


@dataclass
class DrillReport:
    """What a restore drill proved, or failed to."""

    restored_from: Path
    scratch_database: str
    tables_compared: int = 0
    # Without this, "15 tables matched exactly" is also true of fifteen empty
    # tables, and a drill against an empty database would pass proving nothing.
    rows_compared: int = 0
    non_empty_tables: int = 0
    mismatches: list[CountMismatch] = field(default_factory=list)
    archive_digest_matches: bool = False
    error: str | None = None

    @property
    def succeeded(self) -> bool:
        return (
            self.error is None
            and not self.mismatches
            and self.archive_digest_matches
            and self.rows_compared > 0
        )

    def summary(self) -> str:
        if self.error is not None:
            return f"restore drill failed: {self.error}"
        if self.mismatches:
            detail = ", ".join(
                f"{item.table} {item.expected}->{item.restored}" for item in self.mismatches
            )
            return f"row counts differ: {detail}"
        if self.rows_compared == 0:
            return f"{self.tables_compared} tables compared but all empty - proves nothing"
        return (
            f"{self.tables_compared} tables ({self.non_empty_tables} non-empty) matched "
            f"exactly, {self.rows_compared} rows, archive digest "
            f"{'matched' if self.archive_digest_matches else 'DIFFERED'}"
        )


def archive_digest(root: Path) -> tuple[int, str]:
    """A digest over every file in the chain archive, and the file count.

    Filenames are hashed alongside contents, so a file that is renamed or
    missing changes the digest. Hashing contents alone would let a lost
    partition pass unnoticed when another file happened to fill the gap.
    """
    digest = hashlib.sha256()
    count = 0
    for path in sorted(root.rglob("*.parquet")):
        digest.update(str(path.relative_to(root)).encode())
        with path.open("rb") as handle:
            while chunk := handle.read(HASH_CHUNK_BYTES):
                digest.update(chunk)
        count += 1
    return count, digest.hexdigest()


class BackupService:
    """Writes one timestamped backup: a database dump and an archive digest."""

    def __init__(
        self,
        *,
        tool: BackupToolPort,
        clock: Clock,
        notifier: NotifierPort,
        database: str,
        archive_root: Path,
        backup_dir: Path = DEFAULT_BACKUP_DIR,
    ) -> None:
        self._tool = tool
        self._clock = clock
        self._notifier = notifier
        self._database = database
        self._archive_root = archive_root
        self._backup_dir = backup_dir

    def run(self) -> BackupReport:
        taken_at = self._clock.utc_now()
        target = self._backup_dir / taken_at.strftime("%Y%m%dT%H%M%SZ")
        target.mkdir(parents=True, exist_ok=True)

        size = self._tool.dump_to(self._database, target / DUMP_NAME)
        count, digest = archive_digest(self._archive_root)
        (target / MANIFEST_NAME).write_text(f"{digest}  {count} files\n")

        report = BackupReport(
            taken_at=taken_at,
            destination=target,
            dump_bytes=size,
            archive_files=count,
            archive_digest=digest,
        )
        self._notifier.send_alert(AlertLevel.INFO, f"Backup: {report.summary()}")
        return report

    def newest_backup_at(self) -> datetime | None:
        """When the most recent backup was taken, from the directory names."""
        stamps = []
        for path in self._backup_dir.glob("*"):
            if not path.is_dir() or not (path / DUMP_NAME).is_file():
                continue  # a directory with no dump is a failed run, not a backup
            try:
                # The directory name is written in UTC and ends in Z, so the
                # zone is known rather than inherited from whatever the clock
                # happens to return.
                naive = datetime.strptime(path.name, "%Y%m%dT%H%M%SZ")  # noqa: DTZ007
            except ValueError:
                continue
            stamps.append(naive.replace(tzinfo=UTC))
        if not stamps:
            return None
        return max(stamps)

    def check_freshness(self) -> Staleness:
        """Alert if the nightly job has stopped. Task 3 of the step."""
        result = staleness(self.newest_backup_at(), self._clock.utc_now(), DEFAULT_MAX_BACKUP_AGE)
        if result.is_stale:
            self._notifier.send_alert(AlertLevel.CRITICAL, f"Backup missing: {result.reason}")
        return result


class RestoreDrill:
    """Restores a dump into a scratch database and compares it to the live one.

    Never touches the live database except to read row counts. The scratch name
    is checked by `refuse_unsafe_restore` before anything is created, because
    the cost of getting it wrong is the data this whole project exists to
    collect.
    """

    def __init__(
        self,
        *,
        tool: BackupToolPort,
        notifier: NotifierPort,
        database: str,
        archive_root: Path,
    ) -> None:
        self._tool = tool
        self._notifier = notifier
        self._database = database
        self._archive_root = archive_root

    def run(self, backup_dir: Path) -> DrillReport:
        scratch = scratch_name(self._database)
        refuse_unsafe_restore(scratch, self._database)

        report = DrillReport(restored_from=backup_dir, scratch_database=scratch)
        dump = backup_dir / DUMP_NAME
        if not dump.is_file():
            report.error = f"no dump at {dump}"
            return self._report(report)

        try:
            self._tool.drop_database(scratch)  # a leftover from a failed drill
            self._tool.create_database(scratch)
            self._tool.restore_into(scratch, dump)

            tables = self._tool.user_tables(self._database)
            live = self._tool.table_counts(self._database, tables)
            restored = self._tool.table_counts(scratch, tables)

            report.tables_compared = len(tables)
            report.rows_compared = sum(live.values())
            report.non_empty_tables = sum(1 for count in live.values() if count > 0)
            report.mismatches = count_mismatches(live, restored)
            report.archive_digest_matches = self._archive_matches(backup_dir)
        except Exception as error:  # a failed drill must report, not crash
            report.error = f"{type(error).__name__}: {str(error)[:200]}"
        finally:
            # Always dropped. A scratch database left behind is confusing at
            # best and gets backed up itself at worst.
            self._tool.drop_database(scratch)

        return self._report(report)

    def _archive_matches(self, backup_dir: Path) -> bool:
        manifest = backup_dir / MANIFEST_NAME
        if not manifest.is_file():
            return False
        recorded = manifest.read_text().split()[0]
        _, current = archive_digest(self._archive_root)
        return recorded == current

    def _report(self, report: DrillReport) -> DrillReport:
        level = AlertLevel.INFO if report.succeeded else AlertLevel.CRITICAL
        self._notifier.send_alert(level, f"Restore drill: {report.summary()}")
        return report
