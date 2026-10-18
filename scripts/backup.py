"""Take a backup, or run a restore drill against the newest one.

    make backup          # nightly: dump + chain-archive digest
    make restore-drill   # prove the newest backup actually restores

Wired as a module rather than a shell script so it uses the same settings,
clock and notifier as everything else, and so the restore safety check lives
in one place instead of being re-implemented in bash.
"""

from __future__ import annotations

import sys
from pathlib import Path

from trading_desk.adapters.backup.postgres_dump import PostgresBackupTool
from trading_desk.adapters.clock import SystemClock
from trading_desk.adapters.notify.logging_notifier import LoggingNotifier
from trading_desk.application.backup import (
    DEFAULT_BACKUP_DIR,
    DUMP_NAME,
    BackupService,
    RestoreDrill,
)

DATABASE = "trading_desk"
ARCHIVE_ROOT = Path("data/chains")


def _service() -> BackupService:
    clock = SystemClock()
    return BackupService(
        tool=PostgresBackupTool(user=DATABASE),
        clock=clock,
        notifier=LoggingNotifier(clock),
        database=DATABASE,
        archive_root=ARCHIVE_ROOT,
    )


def _newest_backup() -> Path | None:
    candidates = [
        path
        for path in DEFAULT_BACKUP_DIR.glob("*")
        if path.is_dir() and (path / DUMP_NAME).is_file()
    ]
    return max(candidates) if candidates else None


def main() -> int:
    if "--drill" in sys.argv:
        newest = _newest_backup()
        if newest is None:
            print(f"No backup found in {DEFAULT_BACKUP_DIR}. Run `make backup` first.")
            return 1

        clock = SystemClock()
        drill = RestoreDrill(
            tool=PostgresBackupTool(user=DATABASE),
            notifier=LoggingNotifier(clock),
            database=DATABASE,
            archive_root=ARCHIVE_ROOT,
        )
        drill_report = drill.run(newest)
        print(f"Restored from {newest}")
        print(drill_report.summary())
        return 0 if drill_report.succeeded else 1

    report = _service().run()
    print(f"Backup written to {report.destination}")
    print(report.summary())

    freshness = _service().check_freshness()
    print(f"Freshness: {freshness.reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
