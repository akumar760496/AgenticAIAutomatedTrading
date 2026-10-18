"""Backup rules. Pure: no subprocess, no filesystem, no clock.

The decisions here are small but each one has a way of going wrong quietly:

1. **A restore must never target the live database.** The drill restores a dump
   into a scratch database and compares; pointed at the live one it would
   overwrite the only copy of data that cannot be re-fetched. `scratch_name`
   and `refuse_unsafe_restore` make that a checked precondition rather than a
   convention, because the failure is unrecoverable.
2. **A backup nobody checks is not a backup.** `staleness` turns "when did the
   last one run" into a decision with the threshold attached, so a silently
   stopped cron job raises instead of being discovered during a restore.
3. **Row counts alone do not prove a restore.** They catch a truncated table;
   they miss a dump written from the wrong database. The chain archive is
   compared by checksum for that reason.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

# A scratch database carries this suffix and nothing else does. Short, and
# obviously not a production name if one is ever seen in a connection list.
SCRATCH_SUFFIX = "_restore_drill"

# A backup older than this is treated as missing. One day plus a margin: the
# job is nightly, and alerting at 24h exactly would fire on a slow night.
DEFAULT_MAX_BACKUP_AGE = timedelta(hours=30)


class UnsafeRestoreError(RuntimeError):
    """A restore was aimed at something other than a scratch database."""


@dataclass(frozen=True)
class Staleness:
    """Whether the last backup is recent enough, and the numbers behind it."""

    is_stale: bool
    age: timedelta | None
    threshold: timedelta
    reason: str


@dataclass(frozen=True)
class CountMismatch:
    table: str
    expected: int
    restored: int


def scratch_name(live_database: str) -> str:
    """The scratch database a drill may restore into."""
    return f"{live_database}{SCRATCH_SUFFIX}"


def refuse_unsafe_restore(target: str, live_database: str) -> None:
    """Raise unless `target` is the scratch database for `live_database`.

    Checked rather than assumed: a restore into the live database would
    overwrite option-chain history that cannot be re-fetched at any price.
    """
    if target == live_database:
        raise UnsafeRestoreError(
            f"refusing to restore into the live database {target!r} - "
            "a drill restores into a scratch copy"
        )
    if not target.endswith(SCRATCH_SUFFIX):
        raise UnsafeRestoreError(
            f"refusing to restore into {target!r}: a drill target must end in {SCRATCH_SUFFIX!r}"
        )


def staleness(
    newest_backup_at: datetime | None,
    now: datetime,
    threshold: timedelta = DEFAULT_MAX_BACKUP_AGE,
) -> Staleness:
    """How old the newest backup is, and whether that is acceptable.

    `now` is an argument because `domain/` never reads a clock (rule 5).
    """
    if newest_backup_at is None:
        return Staleness(
            is_stale=True, age=None, threshold=threshold, reason="no backup has ever been taken"
        )

    age = now - newest_backup_at
    if age > threshold:
        return Staleness(
            is_stale=True,
            age=age,
            threshold=threshold,
            reason=f"newest backup is {_hours(age)} old, threshold is {_hours(threshold)}",
        )
    return Staleness(
        is_stale=False, age=age, threshold=threshold, reason=f"newest backup is {_hours(age)} old"
    )


def count_mismatches(expected: dict[str, int], restored: dict[str, int]) -> list[CountMismatch]:
    """Tables whose restored row count differs from the live one.

    A table present live and absent from the restore counts as 0 rather than
    being skipped - a missing table is the most important mismatch there is,
    and iterating the restored side would silently omit it.
    """
    mismatches = []
    for table in sorted(expected):
        live_count = expected[table]
        restored_count = restored.get(table, 0)
        if live_count != restored_count:
            mismatches.append(
                CountMismatch(table=table, expected=live_count, restored=restored_count)
            )
    return mismatches


def _hours(span: timedelta) -> str:
    return f"{span.total_seconds() / 3600:.1f}h"
