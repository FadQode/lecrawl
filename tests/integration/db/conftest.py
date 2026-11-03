"""### PostgreSQL fixtures for database integration tests.

The repository ships `docker/postgres/Dockerfile` for a disposable server. When no server is
reachable the tests skip with an explicit reason instead of passing silently.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import TYPE_CHECKING

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, make_url, text
from sqlalchemy.orm import Session, sessionmaker

from lexcrawl.db.engine import create_db_engine
from lexcrawl.db.seed import seed_sources
from lexcrawl.db.session import create_session_factory

if TYPE_CHECKING:
    from lexcrawl.config import Settings

DEFAULT_TEST_URL = "postgresql+psycopg://lexcrawl:lexcrawl@localhost:5432/lexcrawl_test"
MAINTENANCE_DATABASE = "postgres"
TABLES = (
    "crawl_errors",
    "crawl_run_sources",
    "document_localizations",
    "document_resources",
    "documents",
    "crawl_runs",
    "sources",
)


def test_database_url() -> str:
    """### Return the DSN used by the integration suite."""
    return os.environ.get("LEXCRAWL_TEST_DATABASE_URL", DEFAULT_TEST_URL)


def ensure_database(dsn: str) -> None:
    """### Create the test database when the server does not have it yet."""
    url = make_url(dsn)
    database = url.database or "lexcrawl_test"
    with psycopg.connect(
        host=url.host or "localhost",
        port=url.port or 5432,
        user=url.username or "lexcrawl",
        password=url.password or "",
        dbname=MAINTENANCE_DATABASE,
        autocommit=True,
        connect_timeout=5,
    ) as connection:
        exists = connection.execute("select 1 from pg_database where datname = %s", (database,)).fetchone()
        if not exists:
            connection.execute(f'CREATE DATABASE "{database}"')


def alembic_configuration(dsn: str) -> Config:
    """### Build an Alembic configuration bound to the test DSN."""
    configuration = Config("alembic.ini")
    configuration.set_main_option("sqlalchemy.url", dsn)
    return configuration


def settings_for(dsn: str) -> Settings:
    """### Build settings pointing at the test database."""
    from lexcrawl.config import Settings

    return Settings(_env_file=None, database_url=dsn, db_pool_size=2, db_max_overflow=2)


@pytest.fixture(scope="session")
def database_url() -> str:
    """### Skip the suite when PostgreSQL is unreachable, then migrate to head."""
    dsn = test_database_url()
    try:
        ensure_database(dsn)
    except psycopg.Error as error:
        pytest.skip(f"PostgreSQL test database unavailable: {error}")
    command.upgrade(alembic_configuration(dsn), "head")
    return dsn


@pytest.fixture(scope="session")
def engine(database_url: str) -> Iterator[Engine]:
    """### Provide one engine for the whole integration session."""
    created = create_db_engine(settings_for(database_url))
    yield created
    created.dispose()


@pytest.fixture(scope="session")
def session_factory(engine: Engine) -> sessionmaker[Session]:
    """### Provide the session factory bound to the test database."""
    return create_session_factory(engine)


@pytest.fixture(autouse=True)
def clean_database(engine: Engine) -> Iterator[None]:
    """### Truncate every table and seed the registry before each test."""
    with engine.begin() as connection:
        connection.execute(text(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY CASCADE"))
    with engine.begin() as connection:
        seed_sources(Session(bind=connection))
    yield


@pytest.fixture
def db_session(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    """### Provide one session that commits on success and always closes."""
    session = session_factory()
    try:
        yield session
        session.commit()
    finally:
        session.close()
