"""Alembic environment.

The database URL comes from application settings, so migrations always target
the same database the app does.  ``render_as_batch`` is enabled because SQLite
cannot ALTER columns in place; without it, any future column change would fail
on the default development database.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool

from agentbench.config import get_settings
from agentbench.db.models import Base
from agentbench.db.session import get_engine

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata
settings = get_settings()
# `set_main_option` writes through configparser, which treats `%` as
# interpolation syntax. Percent-encoded characters are ordinary in a PostgreSQL
# URL (and in any password containing `%`), so the value must be escaped or
# `alembic upgrade` fails before it connects.
config.set_main_option("sqlalchemy.url", settings.database_url.replace("%", "%%"))


def run_migrations_offline() -> None:
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = get_engine()
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
            compare_type=True,
            poolclass=pool.NullPool,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
