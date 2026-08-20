"""Shared pytest fixtures.

DB-backed tests run on a throwaway SQLite file — the same engine the desktop exe
uses — so the suite needs no external services. The whole process (including the
FastAPI app under test) is redirected to it before the lazy engine is created.
Pure-unit tests simply don't request the `db` fixture and never touch the DB.
"""

import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from shruti_core.models import Base
from shruti_core.settings import get_settings


@pytest.fixture(scope="session")
def test_engine(tmp_path_factory):
    test_url = f"sqlite:///{(tmp_path_factory.mktemp('db') / 'shruti_test.db').as_posix()}"

    # Redirect the entire process (settings cache + lazy engine) to the test DB,
    # so code under test (e.g. the FastAPI app) uses it too. Env vars beat .env in
    # pydantic-settings, so tests stay hermetic even when a local .env exists.
    os.environ["DATABASE_URL"] = test_url
    os.environ["LLM_MODE"] = "stub"
    os.environ["DIARIZE_ENABLED"] = "0"
    os.environ["ASR_BACKEND"] = "faster_whisper"
    os.environ["ASR_MODEL"] = "tiny"
    os.environ["ASR_DEVICE"] = "cpu"
    get_settings.cache_clear()
    import shruti_core.db as db_mod

    db_mod._engine = None
    db_mod._session_factory = None

    engine = create_engine(test_url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture()
def db(test_engine) -> Session:
    """Fresh session against a wiped schema for each test."""
    with test_engine.connect() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(text(f'DELETE FROM "{table.name}"'))
        conn.commit()
    factory = sessionmaker(bind=test_engine, expire_on_commit=False)
    session = factory()
    yield session
    session.close()


@pytest.fixture(scope="session")
def fixtures_dir():
    from pathlib import Path

    return Path(__file__).parent / "fixtures"


@pytest.fixture()
def tmp_storage(monkeypatch, tmp_path):
    """Point storage at a per-test temp dir (settings cache cleared both ways)."""
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path / "storage"))
    get_settings.cache_clear()
    yield tmp_path / "storage"
    get_settings.cache_clear()
