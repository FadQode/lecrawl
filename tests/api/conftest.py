"""### API test fixtures backed by the isolated PostgreSQL test database."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date

import psycopg
import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session, sessionmaker

from lexcrawl.actors.base import ActorRunResult
from lexcrawl.api import create_app
from lexcrawl.db.engine import create_db_engine
from lexcrawl.db.seed import seed_sources
from lexcrawl.db.session import create_session_factory
from lexcrawl.orchestrator import CrawlRunReport
from lexcrawl.persistence import persist_crawl_report
from tests.integration.db.builders import SITE, document, resource, result
from tests.integration.db.conftest import (
    TABLES,
    alembic_configuration,
    ensure_database,
    settings_for,
    test_database_url,
)

TRIGGER_TOKEN = "test-trigger-token"


@pytest.fixture(scope="session")
def api_database_url() -> str:
    """### Skip the API suite when PostgreSQL is unreachable, then migrate to head."""
    dsn = test_database_url()
    try:
        ensure_database(dsn)
    except psycopg.Error as error:
        pytest.skip(f"PostgreSQL test database unavailable: {error}")
    command.upgrade(alembic_configuration(dsn), "head")
    return dsn


@pytest.fixture(scope="session")
def api_engine(api_database_url: str) -> Iterator[Engine]:
    """### Provide one engine for the API test session."""
    engine = create_db_engine(settings_for(api_database_url))
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def api_session_factory(api_engine: Engine) -> sessionmaker[Session]:
    """### Provide the session factory bound to the test database."""
    return create_session_factory(api_engine)


@pytest.fixture(autouse=True)
def clean_database(api_engine: Engine) -> Iterator[None]:
    """### Truncate every table and seed the registry before each test."""
    with api_engine.begin() as connection:
        connection.execute(text(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY CASCADE"))
    with api_engine.begin() as connection:
        seed_sources(Session(bind=connection))
    yield


@pytest.fixture
def db_session(api_session_factory: sessionmaker[Session]) -> Iterator[Session]:
    """### Provide one session that commits on success and always closes."""
    session = api_session_factory()
    try:
        yield session
        session.commit()
    finally:
        session.close()


@pytest.fixture
def client(api_database_url: str) -> Iterator[TestClient]:
    """### Provide a TestClient whose app uses the isolated test database and a trigger token."""
    settings = settings_for(api_database_url).model_copy(
        update={"manual_trigger_token": TRIGGER_TOKEN, "schedule_enabled": False}
    )
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def unconfigured_client(api_database_url: str) -> Iterator[TestClient]:
    """### Provide a TestClient whose app has no trigger token configured."""
    settings = settings_for(api_database_url).model_copy(update={"schedule_enabled": False})
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client


def persist_results(factory: sessionmaker[Session], *results: ActorRunResult) -> None:
    """### Persist one or more actor results into the test database."""
    persist_crawl_report(factory, CrawlRunReport(results=list(results)))


def seed_content(factory: sessionmaker[Session]) -> None:
    """### Persist one UAE article, one CBA release, and one Commerce report."""
    persist_results(
        factory,
        result(
            source="uae_legislation_news",
            documents=(
                document(
                    source="uae_legislation_news",
                    url=f"{SITE}/ar/news/one",
                    native_id="uae-1",
                    title="UAE title",
                    language="ar",
                    published_at=date(2026, 1, 8),
                    resources=(resource(),),
                ),
            ),
        ),
        result(
            source="cba_news_releases",
            documents=(
                document(
                    source="cba_news_releases",
                    url=f"{SITE}/article/two",
                    native_id="cba-2",
                    title="CBA title",
                    language="en",
                    type_of_law="News Releases",
                    published_at=date(2025, 11, 3),
                ),
            ),
        ),
        result(
            source="commerce_reports",
            documents=(
                document(
                    source="commerce_reports",
                    url=f"{SITE}/reports/three",
                    native_id="com-3",
                    title="Report title",
                    language="en",
                    published_at=date(2025, 1, 15),
                ),
            ),
        ),
    )
