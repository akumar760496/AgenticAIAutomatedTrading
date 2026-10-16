"""Database and Redis connections.

Infrastructure only: how to connect, not what is stored. Schema lives in
alembic/versions, and the code that reads and writes rows lives in adapters.

The extensions matter and are checked at startup rather than discovered later:
TimescaleDB turns bars into hypertables, pgvector stores news embeddings for
deduplication. A database missing either looks fine until the first query fails.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import redis
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from trading_desk.infra.config import InfraSettings

REQUIRED_EXTENSIONS = ["timescaledb", "vector"]


def create_db_engine(settings: InfraSettings, echo: bool = False) -> Engine:
    """Build the engine. pool_pre_ping because a laptop sleeping kills idle connections."""
    return create_engine(
        settings.database_url.get_secret_value(),
        echo=echo,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=5,
    )


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    """A session that commits on success and rolls back on any exception.

    Rolls back rather than attempting a partial save: half-written market data is
    worse than none, because nothing downstream can tell it is incomplete.
    """
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def installed_extensions(engine: Engine) -> dict[str, str]:
    """Return {extension_name: version} for what is actually installed."""
    with engine.connect() as connection:
        rows = connection.execute(text("SELECT extname, extversion FROM pg_extension"))
        return dict(rows.all())


def missing_extensions(engine: Engine) -> list[str]:
    installed = installed_extensions(engine)
    return [name for name in REQUIRED_EXTENSIONS if name not in installed]


def create_redis_client(settings: InfraSettings) -> redis.Redis:
    """Redis is transport. decode_responses so callers get str, not bytes."""
    return redis.Redis.from_url(settings.redis_url, decode_responses=True)
