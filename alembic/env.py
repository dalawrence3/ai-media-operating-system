"""Alembic migration environment — PostgreSQL production path.

SQLite schema management is handled by database.py (existing dev/test path).
This env.py manages PostgreSQL schema via explicit version-controlled migrations.

Usage:
  ACE_DATABASE_URL=postgresql://user:pass@host/db alembic upgrade head
  ACE_DATABASE_URL=postgresql://user:pass@host/db alembic current
  ACE_DATABASE_URL=postgresql://user:pass@host/db alembic history
"""

from __future__ import annotations

import os
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Resolve DB URL from environment; ACE_DATABASE_URL takes precedence.
_raw_url = os.environ.get("ACE_DATABASE_URL", config.get_main_option("sqlalchemy.url", ""))

if not _raw_url:
    raise RuntimeError(
        "ACE_DATABASE_URL must be set when running Alembic migrations.\n"
        "Example: ACE_DATABASE_URL=postgresql://user:pass@localhost:5432/ace "
        "alembic upgrade head"
    )

# Map bare postgresql:// → postgresql+psycopg:// (psycopg3 SQLAlchemy driver).
_db_url = _raw_url
for _old, _new in [("postgresql://", "postgresql+psycopg://"), ("postgres://", "postgresql+psycopg://")]:
    if _db_url.startswith(_old):
        _db_url = _new + _db_url[len(_old):]
        break

config.set_main_option("sqlalchemy.url", _db_url)

# No ORM metadata — all migrations use raw SQL via op.execute().
target_metadata = None


def run_migrations_offline() -> None:
    """Emit SQL to stdout (dry-run / review mode)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live PostgreSQL connection."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
