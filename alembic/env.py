"""Alembic environment.

Reads the database URL from our settings rather than alembic.ini, so the credential
stays in .env and out of version control.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from trading_desk.infra.config import load_infra_settings

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# No declarative models yet - migrations are written by hand until there are tables
# worth autogenerating. Hand-written migrations for a time-series schema are clearer
# than autogenerate, which does not know about hypertables.
target_metadata = None

config.set_main_option(
    "sqlalchemy.url",
    load_infra_settings().database_url.get_secret_value(),
)


def run_migrations_offline() -> None:
    """Emit SQL to stdout without connecting. Useful for reviewing a migration."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
