import sys
import os
from pathlib import Path
from logging.config import fileConfig
from sqlalchemy import engine_from_config, pool, event
from alembic import context

# Ensure project root is on sys.path so `app` is importable
_project_root = Path(__file__).parent.parent
sys.path.insert(0, str(_project_root))

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Override DB URL with the absolute data-dir path so alembic always targets
# the right file regardless of the working directory or the ini value.
# Only apply when the URL still contains the relative ini placeholder; callers
# (e.g. conftest.py in tests) may have already replaced it with their own URL.
_current_url = config.get_main_option("sqlalchemy.url") or ""
if "data/collection.db" in _current_url:
    _db_path = _project_root / "data" / "collection.db"
    config.set_main_option("sqlalchemy.url", f"sqlite:///{_db_path}")

# Import models so metadata is populated for autogenerate
from app.models import Base  # noqa: E402

target_metadata = Base.metadata


def _set_pragmas(dbapi_conn, _record):
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA foreign_keys = ON")
    cursor.close()


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    event.listen(connectable, "connect", _set_pragmas)

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
