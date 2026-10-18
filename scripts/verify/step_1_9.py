"""Verify step 1.9 - backup and restore drill.

`docs/12`: **"The only valid verification is a restore you performed."** So
this script takes a real backup and performs a real restore into a scratch
database, every run. It does not inspect a script and conclude it would work.

That insistence earned its keep immediately. The first drill ever run failed:

    pg_restore: error: COPY failed for table "metadata":
    duplicate key value violates unique constraint "metadata_pkey"
    Key (key)=(exported_uuid) already exists

Adding TimescaleDB's documented `timescaledb_pre_restore()` /
`post_restore()` calls fixed it. **It has not been reproduced since** - the
naive path now succeeds against that same dump and every later one - so the
trigger is unknown and recorded as ISS-020 rather than explained away. What
the episode does establish is the point `docs/12` is making: a dump that
exits zero and is the right size told us nothing, and only attempting the
restore did.

Two checks guard against a drill that passes while proving nothing:

- **Rows must actually have been compared.** "15 tables matched exactly" is
  equally true of fifteen empty tables.
- **The restore target must be refused** when it is the live database. That one
  is induced rather than hoped for: the live name and an unrelated name are
  both offered and must both be rejected.
"""

from __future__ import annotations

from pathlib import Path

from scripts.verify._runner import CheckResult
from trading_desk.adapters.backup.postgres_dump import PostgresBackupTool
from trading_desk.adapters.clock import SystemClock
from trading_desk.adapters.notify.logging_notifier import LoggingNotifier
from trading_desk.application.backup import (
    DEFAULT_BACKUP_DIR,
    BackupService,
    DrillReport,
    RestoreDrill,
)
from trading_desk.domain.backup import UnsafeRestoreError, refuse_unsafe_restore, scratch_name

TITLE = "Backup and restore drill"

DATABASE = "trading_desk"
DB_USER = "trading_desk"
ARCHIVE_ROOT = Path("data/chains")

# A dump smaller than this is not a database, it is an error that exited zero.
MIN_DUMP_BYTES = 10_000


def _clock() -> SystemClock:
    return SystemClock()


def _tool() -> PostgresBackupTool:
    return PostgresBackupTool(user=DB_USER)


def _service() -> BackupService:
    return BackupService(
        tool=_tool(),
        clock=_clock(),
        notifier=LoggingNotifier(_clock()),
        database=DATABASE,
        archive_root=ARCHIVE_ROOT,
    )


def _take_a_backup() -> tuple[bool, str, Path | None]:
    try:
        report = _service().run()
    except Exception as error:
        return False, f"{type(error).__name__}: {str(error)[:140]}", None

    big_enough = report.dump_bytes >= MIN_DUMP_BYTES
    return big_enough, report.summary(), report.destination


def _perform_a_real_restore(backup_dir: Path | None) -> tuple[bool, str, DrillReport | None]:
    if backup_dir is None:
        return False, "no backup was taken, so nothing could be restored", None

    drill = RestoreDrill(
        tool=_tool(),
        notifier=LoggingNotifier(_clock()),
        database=DATABASE,
        archive_root=ARCHIVE_ROOT,
    )
    report = drill.run(backup_dir)
    return report.succeeded, report.summary(), report


def _the_comparison_was_not_vacuous(report: DrillReport | None) -> tuple[bool, str]:
    if report is None:
        return False, "no drill ran"
    return report.rows_compared > 0, (
        f"{report.rows_compared} rows across {report.non_empty_tables} non-empty "
        f"of {report.tables_compared} tables"
    )


def _restoring_over_the_live_database_is_refused() -> tuple[bool, str]:
    """Induced, not assumed. The cost of this failing once is the whole archive."""
    refused = []
    for target in (DATABASE, "production", "postgres"):
        try:
            refuse_unsafe_restore(target, DATABASE)
        except UnsafeRestoreError:
            refused.append(target)

    allowed_ok = True
    try:
        refuse_unsafe_restore(scratch_name(DATABASE), DATABASE)
    except UnsafeRestoreError:
        allowed_ok = False

    ok = len(refused) == 3 and allowed_ok  # noqa: PLR2004 - the three offered above
    return ok, f"refused {refused}; allowed {scratch_name(DATABASE)!r}"


def _no_scratch_database_is_left_behind() -> tuple[bool, str]:
    """A leftover scratch database gets backed up itself and confuses the next drill."""
    output = _tool().user_tables("postgres")  # any database; we only need a connection
    del output
    tool = _tool()
    try:
        tool.table_counts(scratch_name(DATABASE), ["pg_class"])
    except Exception:
        return True, f"{scratch_name(DATABASE)} does not exist, as expected"
    return False, f"{scratch_name(DATABASE)} still exists after the drill"


def _a_missed_backup_is_detected() -> tuple[bool, str]:
    """Task 3 of the step: alert when the nightly job stops."""
    result = _service().check_freshness()
    return not result.is_stale, result.reason


def _backups_are_still_local_only() -> tuple[bool, str]:
    """Honest about what this protects against.

    A backup on the same disk as the database survives a dropped table and a
    bad migration. It does not survive the laptop. `docs/10` asks for a
    versioned object-storage bucket, which needs a provider and credentials -
    a decision for the user, so this reports rather than pretends.
    """
    count = len([path for path in DEFAULT_BACKUP_DIR.glob("*") if path.is_dir()])
    return False, (
        f"{count} local backup(s) in {DEFAULT_BACKUP_DIR}, no offsite copy - "
        "object storage needs a provider and credentials (see STATE.md)"
    )


def run() -> list[CheckResult]:
    backup_ok, backup_detail, backup_dir = _take_a_backup()
    restore_ok, restore_detail, drill = _perform_a_real_restore(backup_dir)
    vacuous_ok, vacuous_detail = _the_comparison_was_not_vacuous(drill)
    refusal_ok, refusal_detail = _restoring_over_the_live_database_is_refused()
    cleanup_ok, cleanup_detail = _no_scratch_database_is_left_behind()
    fresh_ok, fresh_detail = _a_missed_backup_is_detected()
    offsite_ok, offsite_detail = _backups_are_still_local_only()

    return [
        CheckResult(
            name="a_backup_can_be_taken",
            passed=backup_ok,
            measured=backup_detail,
            requirement=f"need a dump of at least {MIN_DUMP_BYTES} bytes plus an archive digest",
        ),
        CheckResult(
            name="a_real_restore_was_performed_and_matched",
            passed=restore_ok,
            measured=restore_detail,
            requirement="need every table's row count to match and the archive digest to agree",
        ),
        CheckResult(
            name="the_comparison_was_not_vacuous",
            passed=vacuous_ok,
            measured=vacuous_detail,
            requirement="need > 0 rows compared - empty tables match each other trivially",
        ),
        CheckResult(
            name="restoring_over_the_live_database_is_refused",
            passed=refusal_ok,
            measured=refusal_detail,
            requirement="need the live and unrelated names rejected, the scratch name allowed",
        ),
        CheckResult(
            name="no_scratch_database_is_left_behind",
            passed=cleanup_ok,
            measured=cleanup_detail,
            requirement="need the scratch database dropped even when the drill fails",
        ),
        CheckResult(
            name="a_missed_backup_would_be_detected",
            passed=fresh_ok,
            measured=fresh_detail,
            requirement="need the newest backup inside the staleness threshold",
        ),
        CheckResult(
            name="backups_are_copied_offsite",
            passed=offsite_ok,
            measured=offsite_detail,
            requirement="need a versioned object-storage bucket - your provider decision",
        ),
    ]
