import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from alembic.config import Config
from alembic.command import upgrade

import app.config as config


@pytest.fixture(scope="session")
def engine(tmp_path_factory):
    """Session-scoped engine on a temp file, with migrations applied once."""
    db_file = tmp_path_factory.mktemp("db") / "test.db"
    engine = create_engine(f"sqlite:///{db_file}")

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_conn, _record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.close()

    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", str(engine.url))
    upgrade(cfg, "head")

    yield engine
    engine.dispose()


@pytest.fixture
def session(engine):
    """Function-scoped session; rolls back after each test for isolation."""
    SessionLocal = sessionmaker(engine)
    with SessionLocal() as sess:
        yield sess
        sess.rollback()


@pytest.fixture
def media_env(engine, tmp_path, monkeypatch):
    """Point the media store at a temp dir and yield a session bound to the migrated DB.
    Shared by every test module that stores/attaches media (#48, #149 step 1.6)."""
    monkeypatch.setattr(config, "_instance", config.AppConfig(media_dir=str(tmp_path / "media")))
    SessionLocal = sessionmaker(engine)
    with SessionLocal() as s:
        yield s, tmp_path / "media"
