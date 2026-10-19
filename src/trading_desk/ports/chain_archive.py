"""Where option chain snapshots are written.

A port rather than a direct Parquet call, so the storage format can change without
touching the snapshotter, and so tests can assert what would have been written
without a filesystem.
"""

from __future__ import annotations

from datetime import date
from typing import Protocol

from trading_desk.domain.enums import OptionRight
from trading_desk.domain.market import OptionChainSnapshot
from trading_desk.domain.values import Money, Price


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

    def premium_on(
        self,
        *,
        underlying: str,
        on_date: date,
        expiry: date,
        strike: Price,
        right: OptionRight,
    ) -> Money | None:
        """The mid of bid and ask for one leg on one day, or None.

        Added in step 3.8, because an exit premium is the one thing a
        hypothetical P&L cannot be computed without. **The mid, not the last
        trade**: an illiquid strike's last print can be hours old and on the
        wrong side of the book, which is ISS-007 in its other form.

        `None` means the archive has no snapshot for that leg on that day, and
        the caller must refuse to score rather than substitute anything. Note
        that the archive holds three snapshots a day, so this is the position
        at 15:15 at best - not at the close, and certainly not at the moment a
        level was crossed.
        """
        ...
