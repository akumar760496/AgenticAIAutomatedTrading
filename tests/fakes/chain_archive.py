"""An in-memory chain archive."""

from __future__ import annotations

from trading_desk.domain.market import OptionChainSnapshot


class FakeChainArchive:
    def __init__(self) -> None:
        self.written: list[OptionChainSnapshot] = []
        self.fail_next_write = False

    def write(self, snapshot: OptionChainSnapshot) -> int:
        if self.fail_next_write:
            self.fail_next_write = False
            raise OSError("Simulated disk failure")
        self.written.append(snapshot)
        return sum(1 for strike in snapshot.strikes for side in (strike.call, strike.put) if side)

    def snapshot_count(self, on_date: str, underlying: str) -> int:
        return sum(
            1
            for snapshot in self.written
            if snapshot.underlying.symbol == underlying
            and snapshot.received_at.date().isoformat() == on_date
        )
