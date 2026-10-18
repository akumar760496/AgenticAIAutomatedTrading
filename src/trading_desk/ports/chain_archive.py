"""Where option chain snapshots are written.

A port rather than a direct Parquet call, so the storage format can change without
touching the snapshotter, and so tests can assert what would have been written
without a filesystem.
"""

from __future__ import annotations

from datetime import date
from typing import Protocol

from trading_desk.domain.market import OptionChainSnapshot


class ChainArchivePort(Protocol):
    def write(self, snapshot: OptionChainSnapshot) -> int:
        """Persist one snapshot. Returns the number of rows written.

        Returning a count rather than None is deliberate: the capture-health log
        records it, and a snapshot that wrote zero rows is a failure that would
        otherwise look like a success.
        """
        ...

    def snapshot_count(self, on_date: str, underlying: str) -> int:
        """How many snapshots exist for a day. Used by the completeness check."""
        ...

    def atm_iv_series(self, underlying: str) -> list[tuple[date, float]]:
        """One ATM IV reading per trading day, oldest first.

        The series IV rank is computed from. A day whose snapshot cannot be
        read is absent rather than interpolated - a gap is honest, an invented
        value is not.
        """
        ...
