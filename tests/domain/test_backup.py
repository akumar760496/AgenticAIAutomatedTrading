"""Backup rules. The refusal to restore over the live database is the important one."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from trading_desk.domain.backup import (
    SCRATCH_SUFFIX,
    UnsafeRestoreError,
    count_mismatches,
    refuse_unsafe_restore,
    scratch_name,
    staleness,
)

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)
LIVE = "trading_desk"


class TestRestoreSafety:
    def test_the_scratch_name_is_not_the_live_name(self) -> None:
        assert scratch_name(LIVE) != LIVE
        assert scratch_name(LIVE).endswith(SCRATCH_SUFFIX)

    def test_restoring_into_the_live_database_is_refused(self) -> None:
        """The unrecoverable mistake. Option-chain history cannot be re-fetched
        at any price, so this is a checked precondition, not a convention."""
        with pytest.raises(UnsafeRestoreError, match="live database"):
            refuse_unsafe_restore(LIVE, LIVE)

    def test_restoring_into_an_unrelated_database_is_refused(self) -> None:
        with pytest.raises(UnsafeRestoreError, match="must end in"):
            refuse_unsafe_restore("production", LIVE)

    def test_the_scratch_database_is_allowed(self) -> None:
        refuse_unsafe_restore(scratch_name(LIVE), LIVE)  # must not raise


class TestStaleness:
    def test_a_recent_backup_is_not_stale(self) -> None:
        result = staleness(NOW - timedelta(hours=6), NOW)

        assert not result.is_stale
        assert "6.0h" in result.reason

    def test_an_old_backup_is_stale_and_says_by_how_much(self) -> None:
        result = staleness(NOW - timedelta(hours=50), NOW)

        assert result.is_stale
        assert "50.0h" in result.reason
        assert "30.0h" in result.reason  # the threshold is reported too (rule 9)

    def test_never_having_backed_up_is_stale_not_an_error(self) -> None:
        result = staleness(None, NOW)

        assert result.is_stale
        assert result.age is None
        assert "ever been taken" in result.reason

    def test_the_boundary_is_not_stale_on_the_threshold_itself(self) -> None:
        assert not staleness(NOW - timedelta(hours=30), NOW).is_stale
        assert staleness(NOW - timedelta(hours=30, seconds=1), NOW).is_stale


class TestCountComparison:
    def test_identical_counts_produce_no_mismatch(self) -> None:
        assert count_mismatches({"bars_1d": 940}, {"bars_1d": 940}) == []

    def test_a_short_table_is_reported_with_both_numbers(self) -> None:
        mismatches = count_mismatches({"bars_1d": 940}, {"bars_1d": 12})

        assert len(mismatches) == 1
        assert mismatches[0].expected == 940
        assert mismatches[0].restored == 12

    def test_a_table_missing_from_the_restore_counts_as_zero_not_skipped(self) -> None:
        """Iterating the restored side would make a lost table invisible, which
        is the single most important mismatch there is."""
        mismatches = count_mismatches({"filings": 1541}, {})

        assert len(mismatches) == 1
        assert mismatches[0].restored == 0

    def test_an_extra_table_in_the_restore_is_not_a_mismatch(self) -> None:
        """The scratch database may hold catalogue tables the comparison does
        not ask about. Only what we expected to survive matters."""
        assert count_mismatches({"bars_1d": 5}, {"bars_1d": 5, "extra": 3}) == []
