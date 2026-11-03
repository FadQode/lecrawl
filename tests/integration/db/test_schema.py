"""### Schema, constraint, migration, and seed tests against PostgreSQL."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from alembic import command
from sqlalchemy import Engine, delete, func, insert, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from lexcrawl.db.models import (
    CrawlError,
    CrawlRun,
    CrawlRunSource,
    Document,
    DocumentLocalization,
    DocumentResource,
    Source,
)
from lexcrawl.db.seed import SOURCE_CODES, seed_sources
from lexcrawl.repositories.documents import upsert_document
from lexcrawl.repositories.localizations import upsert_localization
from lexcrawl.repositories.mapping import localization_payloads
from lexcrawl.repositories.resources import upsert_resource

from .builders import document, resource, timestamp
from .conftest import alembic_configuration


def source_id(session: Session, code: str = "uae_legislation_news") -> int:
    """### Return one seeded source identifier."""
    return session.execute(select(Source.id).where(Source.code == code)).scalar_one()


def insert_document(session: Session, **overrides: object) -> int:
    """### Insert a minimal document row without going through repositories."""
    values: dict[str, object] = {
        "source_id": source_id(session),
        "identity_key": "uae_legislation_news:url:https://listing.example.test/ar/news/one",
        "source_url": "https://listing.example.test/ar/news/one",
        "document_type": "news_article",
        "published_precision": "unknown",
        "first_seen_at": timestamp(),
        "last_seen_at": timestamp(),
        "created_at": timestamp(),
        "updated_at": timestamp(),
    }
    values.update(overrides)
    return int(session.execute(insert(Document).values(**values).returning(Document.id)).scalar_one())


def insert_run(session: Session, **overrides: object) -> int:
    """### Insert a minimal crawl run row."""
    values: dict[str, object] = {
        "run_id": uuid.uuid4(),
        "trigger_type": "manual",
        "status": "running",
        "started_at": timestamp(),
        "created_at": timestamp(),
    }
    values.update(overrides)
    return int(session.execute(insert(CrawlRun).values(**values).returning(CrawlRun.id)).scalar_one())


def insert_run_source(session: Session, run_id: int) -> int:
    """### Insert a minimal source outcome row."""
    return int(
        session.execute(
            insert(CrawlRunSource)
            .values(
                crawl_run_id=run_id,
                source_id=source_id(session),
                status="success",
                started_at=timestamp(),
            )
            .returning(CrawlRunSource.id)
        ).scalar_one()
    )


def test_migrations_create_every_table(engine: Engine) -> None:
    """### The migrated schema contains every persistence table."""
    tables = set(inspect(engine).get_table_names())
    assert tables >= {
        "sources",
        "documents",
        "document_localizations",
        "document_resources",
        "crawl_runs",
        "crawl_run_sources",
        "crawl_errors",
        "alembic_version",
    }


def test_seed_is_idempotent(db_session: Session) -> None:
    """### Seeding twice keeps one row per registry entry."""
    assert seed_sources(db_session) == 0
    db_session.flush()
    assert set(db_session.execute(select(Source.code)).scalars()) == set(SOURCE_CODES)
    assert db_session.execute(select(func.count()).select_from(Source)).scalar_one() == 3


def test_duplicate_document_identity_is_rejected(db_session: Session) -> None:
    """### The source-scoped identity is unique."""
    insert_document(db_session)
    with pytest.raises(IntegrityError):
        insert_document(db_session)
    db_session.rollback()


@pytest.mark.parametrize(
    "overrides",
    [
        {"published_precision": "month", "published_month": None, "published_year": 2026},
        {"published_precision": "month", "published_month": 1, "published_year": None},
        {"published_precision": "day", "published_at": None},
        {"published_precision": "unknown", "published_at": datetime(2026, 1, 8).date()},
        {"published_precision": "year", "published_year": 2026, "published_month": 3},
        {"published_month": 13, "published_year": 2026},
        {"published_precision": "century"},
    ],
)
def test_publication_precision_rules_are_enforced(db_session: Session, overrides: dict[str, object]) -> None:
    """### Invalid precision and date combinations are rejected."""
    with pytest.raises(IntegrityError):
        insert_document(db_session, **overrides)
    db_session.rollback()


def test_negative_resource_metadata_is_rejected(db_session: Session) -> None:
    """### Negative sizes cannot be stored."""
    document_id = insert_document(db_session)
    with pytest.raises(IntegrityError):
        db_session.execute(
            insert(DocumentResource).values(
                document_id=document_id,
                identity_key="res:1",
                title="attachment.pdf",
                resource_type="pdf",
                extraction_status="pending",
                file_size_bytes=-1,
                first_seen_at=timestamp(),
                last_seen_at=timestamp(),
                created_at=timestamp(),
                updated_at=timestamp(),
            )
        )
    db_session.rollback()


def test_negative_counters_are_rejected(db_session: Session) -> None:
    """### Run counters cannot go negative."""
    run_id = insert_run(db_session)
    with pytest.raises(IntegrityError):
        db_session.execute(
            insert(CrawlRunSource).values(
                crawl_run_id=run_id,
                source_id=source_id(db_session),
                status="success",
                documents_processed=-1,
                started_at=timestamp(),
            )
        )
    db_session.rollback()


def test_error_attribution_allows_one_reference(db_session: Session) -> None:
    """### An error points at most at one entity."""
    document_id = insert_document(db_session)
    other_document = insert_document(
        db_session, identity_key="other", source_url="https://listing.example.test/ar/news/other"
    )
    run_source_id = insert_run_source(db_session, insert_run(db_session))
    with pytest.raises(IntegrityError):
        db_session.execute(
            insert(CrawlError).values(
                crawl_run_source_id=run_source_id,
                document_id=document_id,
                localization_id=other_document,
                stage="validate",
                error_type="example",
                message="two references",
                occurred_at=timestamp(),
            )
        )
    db_session.rollback()


def test_finished_timestamps_cannot_precede_start(db_session: Session) -> None:
    """### Execution timestamps must be ordered."""
    start = datetime.now(tz=UTC)
    with pytest.raises(IntegrityError):
        insert_run(db_session, started_at=start, finished_at=start - timedelta(seconds=5))
    db_session.rollback()


def test_deleting_a_document_cascades_to_content(db_session: Session) -> None:
    """### Localizations and resources are removed with their document."""
    canonical = document(resources=(resource(),))
    document_id = upsert_document(db_session, source_id=source_id(db_session), document=canonical, now=timestamp())
    for payload in localization_payloads(canonical):
        upsert_localization(db_session, document_id=document_id, payload=payload, now=timestamp())
    upsert_resource(db_session, document_id=document_id, resource=canonical.resources[0], now=timestamp())
    db_session.flush()

    db_session.execute(delete(Document).where(Document.id == document_id))
    db_session.flush()
    assert db_session.execute(select(func.count()).select_from(DocumentLocalization)).scalar_one() == 0
    assert db_session.execute(select(func.count()).select_from(DocumentResource)).scalar_one() == 0


def test_deleting_a_run_cascades_to_sources_and_errors(db_session: Session) -> None:
    """### Run sources and their errors are removed with the run."""
    run_id = insert_run(db_session)
    run_source_id = insert_run_source(db_session, run_id)
    db_session.execute(
        insert(CrawlError).values(
            crawl_run_source_id=run_source_id,
            stage="unknown",
            error_type="actor_error",
            message="failure",
            occurred_at=timestamp(),
        )
    )
    db_session.flush()
    db_session.execute(delete(CrawlRun).where(CrawlRun.id == run_id))
    db_session.flush()
    assert db_session.execute(select(func.count()).select_from(CrawlRunSource)).scalar_one() == 0
    assert db_session.execute(select(func.count()).select_from(CrawlError)).scalar_one() == 0


def test_migrations_downgrade_and_upgrade_round_trip(engine: Engine, database_url: str) -> None:
    """### The revisions can be applied, reverted, and re-applied."""
    configuration = alembic_configuration(database_url)
    command.downgrade(configuration, "base")
    command.upgrade(configuration, "head")
    assert "documents" in inspect(engine).get_table_names()


def test_restricted_source_deletion_is_refused(db_session: Session) -> None:
    """### A source with documents cannot be deleted."""
    insert_document(db_session)
    identifier = source_id(db_session)
    with pytest.raises(IntegrityError):
        db_session.execute(delete(Source).where(Source.id == identifier))
    db_session.rollback()
