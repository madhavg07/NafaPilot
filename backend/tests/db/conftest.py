"""Database test fixtures.

Every test gets a freshly migrated schema (Alembic upgrade head):
- SQLite: a temporary file database, always.
- PostgreSQL: only when TEST_DATABASE_URL is set (e.g. the docker-compose database).
  The public schema is dropped and recreated before and after each test, so use a
  dedicated test database. (A plain downgrade is not used for cleanup: migration 0003
  refuses to drop simulated-execution records, by design.)
No test calls Gemini.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.data import DataRepository, get_repository
from app.db import get_session, make_engine, make_sessionmaker
from app.main import app
from app.synthetic import generate

BACKEND = Path(__file__).resolve().parents[2]
PG_URL = os.environ.get("TEST_DATABASE_URL")


def alembic_config(url: str) -> Config:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


@pytest.fixture(params=["sqlite", "postgresql"])
def db_url(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[str]:
    if request.param == "sqlite":
        url = f"sqlite:///{tmp_path / 'test.db'}"
    else:
        if not PG_URL:
            pytest.skip("set TEST_DATABASE_URL to run PostgreSQL tests")
        url = PG_URL
        _reset_pg(url)
    command.upgrade(alembic_config(url), "head")
    yield url
    if request.param == "postgresql":
        _reset_pg(url)


def _reset_pg(url: str) -> None:
    eng = make_engine(url)
    with eng.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    eng.dispose()


@pytest.fixture
def engine(db_url: str) -> Iterator[Engine]:
    eng = make_engine(db_url)
    yield eng
    eng.dispose()


@pytest.fixture
def sessions(engine: Engine) -> sessionmaker[Session]:
    return make_sessionmaker(engine)


@pytest.fixture(scope="session")
def repo() -> DataRepository:
    return DataRepository.from_tables(generate(42, include_test_merchants=True).tables)


@pytest.fixture
def client(repo: DataRepository, sessions: sessionmaker[Session]) -> Iterator[TestClient]:
    def session_dep() -> Iterator[Session]:
        s = sessions()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_repository] = lambda: repo
    app.dependency_overrides[get_session] = session_dep
    yield TestClient(app)
    app.dependency_overrides.clear()
