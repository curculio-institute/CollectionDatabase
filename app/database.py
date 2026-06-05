from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker, Session


def get_engine(db_url: str = "sqlite:///collection.db"):
    engine = create_engine(db_url)

    @event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_conn, _record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.execute("PRAGMA journal_mode = WAL")
        cursor.close()

    return engine


def get_session_factory(engine) -> sessionmaker:
    return sessionmaker(bind=engine, class_=Session)
