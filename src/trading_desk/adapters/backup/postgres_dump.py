"""pg_dump and pg_restore, run against the Postgres container.

The tools are not on the host - the database runs in Docker - so every call
goes through `docker compose exec`. The prefix is a constructor argument rather
than a constant so the same adapter works against a managed Postgres later by
passing an empty prefix, which is the only difference between the two.

`--clean --if-exists` is deliberately **absent** from the restore. Those flags
make a restore drop objects in the target first, which is exactly the behaviour
that turns a mistyped target into data loss. The drill creates an empty scratch
database instead, so there is nothing to clean.

No password appears in an argument list here: `docker compose exec` inherits
the container's own trust, and a URL carrying a password never reaches argv
where `ps` would show it (CLAUDE.md rule 8).
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PREFIX = ("docker", "compose", "exec", "-T", "postgres")
DEFAULT_TIMEOUT_SECONDS = 600


class BackupCommandError(RuntimeError):
    """A pg tool exited non-zero. Carries stderr, never a credential."""


@dataclass(frozen=True)
class CommandResult:
    stdout: str
    stderr: str


class PostgresBackupTool:
    """Runs the Postgres command-line tools. One job, no orchestration."""

    def __init__(
        self,
        user: str,
        prefix: tuple[str, ...] = DEFAULT_PREFIX,
        timeout: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._user = user
        self._prefix = prefix
        self._timeout = timeout

    def dump_to(self, database: str, destination: Path) -> int:
        """Write a custom-format dump. Returns its size in bytes.

        Custom format rather than plain SQL because it restores selectively and
        compresses, and because a plain-SQL dump invites being piped into the
        wrong database by hand.
        """
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(self._run(["pg_dump", "-U", self._user, "-d", database, "-F", "c"]))
        return destination.stat().st_size

    def create_database(self, name: str) -> None:
        self._run_text(["createdb", "-U", self._user, name])

    def drop_database(self, name: str) -> None:
        self._run_text(["dropdb", "-U", self._user, "--if-exists", name])

    def restore_into(self, database: str, source: Path) -> None:
        """Restore a dump into an existing, empty database.

        The dump arrives on stdin rather than as a path, because the file lives
        on the host and the tool runs in the container.

        **The TimescaleDB calls around the restore are defensive, and the
        reason is an honest "we saw it once and cannot reproduce it".** The
        first drill ever run failed partway through with

            COPY failed for table "metadata":
            duplicate key value violates unique constraint "metadata_pkey"
            Key (key)=(exported_uuid) already exists

        `timescaledb_pre_restore()` / `post_restore()` are Timescale's
        documented procedure for exactly that, and adding them fixed it. But
        re-running the naive path afterwards against that same dump - and
        every later one - succeeded, so the failure has not been reproduced
        and its trigger is unknown (ISS-020). The calls stay because they are
        cheap, documented and harmless; the claim that they are *required* is
        not one this codebase can currently support.
        """
        self._sql(database, "CREATE EXTENSION IF NOT EXISTS timescaledb")
        self._sql(database, "SELECT timescaledb_pre_restore()")
        try:
            self._run(
                ["pg_restore", "-U", self._user, "-d", database, "--no-owner"],
                stdin=source.read_bytes(),
            )
        finally:
            # Always, even on a failed restore: an extension left in the
            # restoring state makes the next attempt fail for a different and
            # much more confusing reason.
            self._sql(database, "SELECT timescaledb_post_restore()")

    def _sql(self, database: str, statement: str) -> str:
        return self._run_text(
            ["psql", "-U", self._user, "-d", database, "-t", "-A", "-c", statement]
        )

    def table_counts(self, database: str, tables: list[str]) -> dict[str, int]:
        """Row count per table. A table that does not exist counts as absent."""
        counts: dict[str, int] = {}
        for table in tables:
            output = self._run_text(
                [
                    "psql",
                    "-U",
                    self._user,
                    "-d",
                    database,
                    "-t",
                    "-A",
                    "-c",
                    f"SELECT count(*) FROM {table}",
                ]
            )
            text = output.strip()
            if text.isdigit():
                counts[table] = int(text)
        return counts

    def user_tables(self, database: str) -> list[str]:
        output = self._run_text(
            [
                "psql",
                "-U",
                self._user,
                "-d",
                database,
                "-t",
                "-A",
                "-c",
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename",
            ]
        )
        return [line.strip() for line in output.splitlines() if line.strip()]

    def _run_text(self, command: list[str]) -> str:
        return self._run(command).decode(errors="replace")

    def _run(self, command: list[str], stdin: bytes | None = None) -> bytes:
        """One tool invocation. Two callers want bytes, the rest want text, so
        this returns bytes and `_run_text` decodes - rather than a return type
        that changes with a flag."""
        completed = subprocess.run(
            [*self._prefix, *command],
            capture_output=True,
            input=stdin,
            timeout=self._timeout,
            check=False,
        )
        if completed.returncode != 0:
            raise BackupCommandError(
                f"{command[0]} exited {completed.returncode}: "
                f"{completed.stderr.decode(errors='replace')[:200]}"
            )
        return completed.stdout
