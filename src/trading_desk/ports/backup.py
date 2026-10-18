"""What we need from the database backup tooling.

A port because `application/backup.py` must not import an adapter - the
dependency rule points inward (CLAUDE.md rule 2), and import-linter caught the
direct import the moment it was written. It also means the restore drill can be
tested against a fake that records what it was asked to do, without a Postgres
container and without any chance of touching a real database.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol


class BackupToolPort(Protocol):
    def dump_to(self, database: str, destination: Path) -> int:
        """Write a dump of `database`. Returns its size in bytes."""
        ...

    def create_database(self, name: str) -> None: ...

    def drop_database(self, name: str) -> None: ...

    def restore_into(self, database: str, source: Path) -> None:
        """Restore a dump into an existing database.

        Callers must have checked the target first - see
        `domain.backup.refuse_unsafe_restore`. This port cannot enforce it
        because it does not know which database is the live one.
        """
        ...

    def table_counts(self, database: str, tables: list[str]) -> dict[str, int]:
        """Row count per table. A table that does not exist is absent."""
        ...

    def user_tables(self, database: str) -> list[str]: ...
