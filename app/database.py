from pathlib import Path
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker, Session

_DATA_DIR = Path(__file__).parent.parent / "data"
_DATA_DIR.mkdir(exist_ok=True)
_DEFAULT_DB_URL = f"sqlite:///{_DATA_DIR / 'collection.db'}"

_engines: dict[str, object] = {}


def get_engine(db_url: str = _DEFAULT_DB_URL):
    """Return (and cache) the engine for *db_url*. Calling twice returns the same object."""
    if db_url in _engines:
        return _engines[db_url]

    engine = create_engine(db_url)

    @event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_conn, _record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.execute("PRAGMA journal_mode = WAL")
        cursor.close()

    _engines[db_url] = engine
    return engine


def get_session_factory(engine) -> sessionmaker:
    return sessionmaker(bind=engine, class_=Session)
