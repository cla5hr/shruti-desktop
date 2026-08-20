"""Engine/session factory. One engine per process, created lazily."""

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from shruti_core.settings import get_settings

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    global _engine, _session_factory
    if _engine is None:
        # One process, API thread + worker thread share the SQLite file. WAL lets a
        # read proceed while the worker writes; busy_timeout rides out the moments
        # both commit at once.
        url = get_settings().database_url
        _engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 15})

        from sqlalchemy import event

        @event.listens_for(_engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _record):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA busy_timeout=15000")
            cur.close()

        _session_factory = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


def new_session() -> Session:
    get_engine()
    assert _session_factory is not None
    return _session_factory()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Commit on success, rollback on error, always close."""
    session = new_session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
