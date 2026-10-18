"""Verify step 0.5 - local infrastructure.

Connects to the real Postgres and Redis. If they are not running the checks fail
rather than skip: "the database was not up" is a failure of this step, not an
excuse for it.
"""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text

from scripts.verify._runner import CheckResult
from trading_desk.infra.config import load_infra_settings
from trading_desk.infra.db import (
    REQUIRED_EXTENSIONS,
    create_db_engine,
    create_redis_client,
    installed_extensions,
)

TITLE = "Local infrastructure"

ROOT = Path(__file__).resolve().parents[2]


def _extensions() -> tuple[dict[str, str], str]:
    """Returns the installed extensions, or an empty dict and the connection error."""
    try:
        engine = create_db_engine(load_infra_settings())
        return installed_extensions(engine), ""
    except Exception as error:
        return {}, type(error).__name__


def _head_revision() -> str:
    """The newest revision in alembic/versions, read from the scripts themselves.

    Hardcoding the expected revision here meant this check failed the moment a
    second migration landed - it was asserting "0001 is applied" when it meant
    "the database is up to date". Reading the head keeps it true as migrations
    are added, and still fails when the database is behind.
    """
    config = Config(str(ROOT / "alembic.ini"))
    directory = ScriptDirectory.from_config(config)
    head = directory.get_current_head()
    return head or ""


def _applied_migrations() -> list[str]:
    try:
        engine = create_db_engine(load_infra_settings())
        with engine.connect() as connection:
            rows = connection.execute(text("SELECT version_num FROM alembic_version"))
            return [row[0] for row in rows]
    except Exception:
        return []


def _redis_responds() -> bool:
    try:
        return bool(create_redis_client(load_infra_settings()).ping())
    except Exception:
        return False


def _redis_has_no_persistence() -> tuple[bool, str]:
    """Redis is transport. Durable state belongs in Postgres, so saving is off."""
    try:
        client = create_redis_client(load_infra_settings())
        save = str(client.config_get("save").get("save", "unknown"))
        appendonly = str(client.config_get("appendonly").get("appendonly", "unknown"))
        return (save.strip() == "" and appendonly == "no"), f"save='{save}' appendonly={appendonly}"
    except Exception:
        return False, "unreachable"


def run() -> list[CheckResult]:
    extensions, connect_error = _extensions()
    missing = [name for name in REQUIRED_EXTENSIONS if name not in extensions]
    migrations = _applied_migrations()
    head = _head_revision()
    no_persistence, persistence_detail = _redis_has_no_persistence()

    return [
        CheckResult(
            name="compose_file_present",
            passed=(ROOT / "docker-compose.yml").is_file(),
            measured=str((ROOT / "docker-compose.yml").is_file()),
            requirement="need true",
        ),
        CheckResult(
            name="postgres_reachable",
            passed=bool(extensions),
            measured="connected" if extensions else f"failed: {connect_error}",
            requirement="need a connection",
        ),
        CheckResult(
            name="timescaledb_installed",
            passed="timescaledb" in extensions,
            measured=extensions.get("timescaledb", "absent"),
            requirement="need the extension version",
        ),
        CheckResult(
            name="pgvector_installed",
            passed="vector" in extensions,
            measured=extensions.get("vector", "absent"),
            requirement="need the extension version",
        ),
        CheckResult(
            name="required_extensions",
            passed=not missing,
            measured=f"missing {missing}" if missing else "all present",
            requirement=f"need {REQUIRED_EXTENSIONS}",
        ),
        CheckResult(
            name="migrations_applied",
            passed=migrations == [head],
            measured=f"{migrations or 'none'}, head is {head or 'unknown'}",
            requirement="need the database at the head revision",
        ),
        CheckResult(
            name="redis_reachable",
            passed=_redis_responds(),
            measured=str(_redis_responds()),
            requirement="need true",
        ),
        CheckResult(
            name="redis_not_persisting",
            passed=no_persistence,
            measured=persistence_detail,
            requirement="need save='' and appendonly=no",
        ),
    ]
