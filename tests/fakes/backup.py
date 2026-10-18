"""An in-memory backup tool. Records what it was asked to do, touches nothing."""

from __future__ import annotations

from pathlib import Path


class FakeBackupTool:
    """Simulates pg_dump/pg_restore over a dict of row counts.

    `restored` starts as a copy of `live` on restore, so the happy path matches
    by construction. Mutate `restored` afterwards to simulate a truncated
    table - which is what a bad backup actually looks like, and the thing the
    drill exists to catch.
    """

    def __init__(self, live: dict[str, int] | None = None) -> None:
        self.live: dict[str, int] = dict(live or {})
        self.restored: dict[str, int] = {}
        self.created: list[str] = []
        self.dropped: list[str] = []
        self.restored_into: list[str] = []
        self.dump_bytes = 700_000
        self.fail_next_restore = False

    def dump_to(self, database: str, destination: Path) -> int:
        del database
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"x" * self.dump_bytes)
        return self.dump_bytes

    def create_database(self, name: str) -> None:
        self.created.append(name)

    def drop_database(self, name: str) -> None:
        self.dropped.append(name)

    def restore_into(self, database: str, source: Path) -> None:
        del source
        if self.fail_next_restore:
            self.fail_next_restore = False
            raise RuntimeError("Simulated pg_restore failure")
        self.restored_into.append(database)
        self.restored = dict(self.live)

    def table_counts(self, database: str, tables: list[str]) -> dict[str, int]:
        source = self.live if database not in self.restored_into else self.restored
        return {table: source[table] for table in tables if table in source}

    def user_tables(self, database: str) -> list[str]:
        del database
        return sorted(self.live)
