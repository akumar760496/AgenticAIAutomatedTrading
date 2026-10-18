"""The restore drill, against a fake. No Postgres, no chance of touching real data."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from tests.fakes import FakeBackupTool, FakeNotifier, FrozenClock
from trading_desk.application.backup import (
    DUMP_NAME,
    MANIFEST_NAME,
    BackupService,
    RestoreDrill,
    archive_digest,
)
from trading_desk.domain.backup import scratch_name
from trading_desk.ports.notifier import AlertLevel

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)
LIVE = "trading_desk"
LIVE_COUNTS = {"bars_1d": 940, "filings": 1541, "articles": 25}


def _archive(root: Path, files: int = 2) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for index in range(files):
        (root / f"snapshot_{index}.parquet").write_bytes(b"chain-data-%d" % index)
    return root


def _backup(tmp_path: Path, tool: FakeBackupTool, notifier: FakeNotifier) -> BackupService:
    return BackupService(
        tool=tool,
        clock=FrozenClock(NOW),
        notifier=notifier,
        database=LIVE,
        archive_root=_archive(tmp_path / "chains"),
        backup_dir=tmp_path / "backups",
    )


def _drill(tmp_path: Path, tool: FakeBackupTool, notifier: FakeNotifier) -> RestoreDrill:
    return RestoreDrill(
        tool=tool, notifier=notifier, database=LIVE, archive_root=tmp_path / "chains"
    )


class TestTakingABackup:
    def test_a_backup_writes_a_dump_and_a_digest(self, tmp_path: Path) -> None:
        tool, notifier = FakeBackupTool(LIVE_COUNTS), FakeNotifier()

        report = _backup(tmp_path, tool, notifier).run()

        assert (report.destination / DUMP_NAME).is_file()
        assert (report.destination / MANIFEST_NAME).is_file()
        assert report.archive_files == 2

    def test_the_newest_backup_time_is_read_back(self, tmp_path: Path) -> None:
        tool, notifier = FakeBackupTool(LIVE_COUNTS), FakeNotifier()
        service = _backup(tmp_path, tool, notifier)
        service.run()

        assert service.newest_backup_at() == NOW

    def test_no_backup_yet_is_reported_as_critical(self, tmp_path: Path) -> None:
        tool, notifier = FakeBackupTool(LIVE_COUNTS), FakeNotifier()

        result = _backup(tmp_path, tool, notifier).check_freshness()

        assert result.is_stale
        assert notifier.alerts[-1][0] is AlertLevel.CRITICAL


class TestTheDrill:
    def test_a_matching_restore_succeeds(self, tmp_path: Path) -> None:
        tool, notifier = FakeBackupTool(LIVE_COUNTS), FakeNotifier()
        backup = _backup(tmp_path, tool, notifier).run()

        report = _drill(tmp_path, tool, notifier).run(backup.destination)

        assert report.succeeded
        assert report.rows_compared == sum(LIVE_COUNTS.values())

    def test_a_truncated_table_is_caught(self, tmp_path: Path) -> None:
        """What a bad backup actually looks like: it restores, just short."""
        tool, notifier = FakeBackupTool(LIVE_COUNTS), FakeNotifier()
        backup = _backup(tmp_path, tool, notifier).run()
        drill = _drill(tmp_path, tool, notifier)

        original_restore = tool.restore_into

        def restore_then_lose_rows(database: str, source: Path) -> None:
            original_restore(database, source)
            tool.restored["filings"] = 12

        tool.restore_into = restore_then_lose_rows  # type: ignore[method-assign]
        report = drill.run(backup.destination)

        assert not report.succeeded
        assert [item.table for item in report.mismatches] == ["filings"]
        assert notifier.alerts[-1][0] is AlertLevel.CRITICAL

    def test_an_all_empty_comparison_does_not_count_as_success(self, tmp_path: Path) -> None:
        """Fifteen empty tables match fifteen empty tables and prove nothing."""
        tool, notifier = FakeBackupTool({}), FakeNotifier()
        backup = _backup(tmp_path, tool, notifier).run()

        report = _drill(tmp_path, tool, notifier).run(backup.destination)

        assert report.rows_compared == 0
        assert not report.succeeded
        assert "proves nothing" in report.summary()

    def test_a_changed_archive_fails_the_digest_check(self, tmp_path: Path) -> None:
        tool, notifier = FakeBackupTool(LIVE_COUNTS), FakeNotifier()
        backup = _backup(tmp_path, tool, notifier).run()
        (tmp_path / "chains" / "snapshot_0.parquet").write_bytes(b"tampered")

        report = _drill(tmp_path, tool, notifier).run(backup.destination)

        assert not report.archive_digest_matches
        assert not report.succeeded

    def test_a_failed_restore_still_drops_the_scratch_database(self, tmp_path: Path) -> None:
        """A leftover scratch database gets backed up itself at worst."""
        tool, notifier = FakeBackupTool(LIVE_COUNTS), FakeNotifier()
        backup = _backup(tmp_path, tool, notifier).run()
        tool.fail_next_restore = True

        report = _drill(tmp_path, tool, notifier).run(backup.destination)

        assert not report.succeeded
        assert tool.dropped.count(scratch_name(LIVE)) == 2  # before and after

    def test_a_missing_dump_is_reported_not_raised(self, tmp_path: Path) -> None:
        tool, notifier = FakeBackupTool(LIVE_COUNTS), FakeNotifier()

        report = _drill(tmp_path, tool, notifier).run(tmp_path / "nope")

        assert not report.succeeded
        assert "no dump" in report.summary()

    def test_the_drill_never_restores_into_the_live_database(self, tmp_path: Path) -> None:
        tool, notifier = FakeBackupTool(LIVE_COUNTS), FakeNotifier()
        backup = _backup(tmp_path, tool, notifier).run()

        _drill(tmp_path, tool, notifier).run(backup.destination)

        assert tool.restored_into == [scratch_name(LIVE)]
        assert LIVE not in tool.created
        assert LIVE not in tool.dropped

    @pytest.mark.parametrize("database", ["trading_desk", "prod", "postgres", "a"])
    def test_the_target_is_never_the_live_database_whatever_it_is_called(
        self, tmp_path: Path, database: str
    ) -> None:
        """The guard inside RestoreDrill is unreachable by construction, because
        `scratch_name` always appends a suffix - so the property worth asserting
        is that the computed target is never the live name, for any name. The
        guard itself stays as defence against a future caller that builds the
        target some other way, and is induced directly in the domain tests."""
        tool, notifier = FakeBackupTool(LIVE_COUNTS), FakeNotifier()
        backup = _backup(tmp_path, tool, notifier).run()
        drill = RestoreDrill(
            tool=tool, notifier=notifier, database=database, archive_root=tmp_path / "chains"
        )

        drill.run(backup.destination)

        assert database not in tool.restored_into
        assert database not in tool.created
        assert tool.restored_into == [scratch_name(database)]


def test_the_archive_digest_notices_a_renamed_file(tmp_path: Path) -> None:
    """Hashing contents alone would let a lost partition pass if another file
    happened to fill the gap, so the path is hashed too."""
    root = _archive(tmp_path / "chains")
    _, before = archive_digest(root)
    (root / "snapshot_0.parquet").rename(root / "renamed.parquet")
    _, after = archive_digest(root)

    assert before != after
