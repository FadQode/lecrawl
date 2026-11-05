"""### Repository, idempotency, isolation, and concurrency tests."""

from __future__ import annotations

import threading
from datetime import datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from lexcrawl.actors.base import ActorRunResult
from lexcrawl.db.models import (
    CrawlError,
    CrawlRun,
    CrawlRunSource,
    Document,
    DocumentLocalization,
    DocumentResource,
    Source,
)
from lexcrawl.db.seed import source_id_for
from lexcrawl.orchestrator import CrawlRunReport
from lexcrawl.persistence import (
    PersistedSourceRun,
    PersistenceError,
    aggregate_status,
    persist_crawl_report,
    persist_source_result,
    redact_secrets,
)
from lexcrawl.repositories.crawl_runs import create_run
from lexcrawl.repositories.documents import incomplete_reports, upsert_document
from lexcrawl.repositories.localizations import upsert_localization
from lexcrawl.repositories.mapping import localization_payloads
from lexcrawl.repositories.resources import upsert_resource

from .builders import document, resource, result, timestamp, translation

SITE = "https://listing.example.test"


def source_id(session: Session, code: str = "uae_legislation_news") -> int:
    """### Return one seeded source identifier."""
    return session.execute(select(Source.id).where(Source.code == code)).scalar_one()


def new_run(session: Session) -> int:
    """### Create one crawl run and return its primary key."""
    run = create_run(session, trigger_type="manual", started_at=timestamp())
    return run.id


def test_same_identity_updates_one_document(db_session: Session) -> None:
    """### Re-persisting the same identity updates instead of inserting."""
    first = document()
    document_id = upsert_document(db_session, source_id=source_id(db_session), document=first, now=timestamp())
    db_session.flush()
    first_seen = db_session.get(Document, document_id).first_seen_at

    later = timestamp() + timedelta(hours=1)
    same_id = upsert_document(
        db_session,
        source_id=source_id(db_session),
        document=document(title="عنوان محدث"),
        now=later,
    )
    db_session.flush()
    stored = db_session.get(Document, same_id)
    assert same_id == document_id
    assert stored.first_seen_at == first_seen
    assert stored.last_seen_at == later
    assert db_session.execute(select(func.count()).select_from(Document)).scalar_one() == 1


def test_native_identity_survives_a_url_change(db_session: Session) -> None:
    """### A source-native identifier keeps identity when the URL changes."""
    first = document(source="commerce_reports", native_id="n-1", url=f"{SITE}/reports/2026/01/first")
    identifier = upsert_document(
        db_session, source_id=source_id(db_session, "commerce_reports"), document=first, now=timestamp()
    )
    db_session.flush()

    moved = document(source="commerce_reports", native_id="n-1", url=f"{SITE}/reports/2026/02/moved")
    second = upsert_document(
        db_session, source_id=source_id(db_session, "commerce_reports"), document=moved, now=timestamp()
    )
    db_session.flush()
    assert second == identifier
    assert db_session.get(Document, identifier).source_url == moved.source_url


def test_identical_content_in_two_sources_is_two_documents(db_session: Session) -> None:
    """### Content never determines identity."""
    text = "same body"
    first = document(source="uae_legislation_news", text=text)
    second = document(source="cba_news_releases", url=f"{SITE}/article/one", text=text)
    upsert_document(
        db_session, source_id=source_id(db_session, "uae_legislation_news"), document=first, now=timestamp()
    )
    upsert_document(db_session, source_id=source_id(db_session, "cba_news_releases"), document=second, now=timestamp())
    db_session.flush()
    assert db_session.execute(select(func.count()).select_from(Document)).scalar_one() == 2


def test_documents_without_native_ids_are_unique_by_url(db_session: Session) -> None:
    """### Missing native identifiers fall back to deterministic URLs."""
    for slug in ("one", "two"):
        upsert_document(
            db_session,
            source_id=source_id(db_session),
            document=document(url=f"{SITE}/ar/news/{slug}"),
            now=timestamp(),
        )
    db_session.flush()
    keys = set(db_session.execute(select(Document.identity_key)).scalars())
    assert keys == {f"uae_legislation_news:url:{SITE}/ar/news/one", f"uae_legislation_news:url:{SITE}/ar/news/two"}


def test_arabic_and_english_share_one_document(db_session: Session) -> None:
    """### Localizations belong to the same logical article."""
    canonical = document(translations=(translation(),))
    document_id = upsert_document(db_session, source_id=source_id(db_session), document=canonical, now=timestamp())
    for payload in localization_payloads(canonical):
        upsert_localization(db_session, document_id=document_id, payload=payload, now=timestamp())
    db_session.flush()

    rows = db_session.execute(select(DocumentLocalization).order_by(DocumentLocalization.language_code)).scalars().all()
    assert [(row.language_code, row.status) for row in rows] == [("ar", "complete"), ("en", "complete")]
    assert all(row.document_id == document_id for row in rows)


def test_failed_refresh_preserves_last_successful_content(db_session: Session) -> None:
    """### A blocked refresh keeps content, hash, and fetch time while advancing the attempt."""
    canonical = document(translations=(translation(),))
    document_id = upsert_document(db_session, source_id=source_id(db_session), document=canonical, now=timestamp())
    payloads = localization_payloads(canonical)
    for payload in payloads:
        upsert_localization(db_session, document_id=document_id, payload=payload, now=timestamp())
    db_session.flush()
    success = db_session.execute(
        select(DocumentLocalization).where(DocumentLocalization.language_code == "en")
    ).scalar_one()
    original_hash = success.content_hash
    original_fetch = success.fetched_at

    later = timestamp() + timedelta(hours=2)
    blocked = document(translations=(translation(text=None, status="access_blocked", error="403 from source"),))
    for payload in localization_payloads(blocked):
        upsert_localization(db_session, document_id=document_id, payload=payload, now=later)
    db_session.flush()
    db_session.expire_all()

    refreshed = db_session.execute(
        select(DocumentLocalization).where(DocumentLocalization.language_code == "en")
    ).scalar_one()
    assert refreshed.status == "access_blocked"
    assert refreshed.error == "403 from source"
    assert refreshed.content_hash == original_hash
    assert refreshed.text_content is not None
    assert refreshed.fetched_at == original_fetch
    assert refreshed.last_attempt_at == later


def test_duplicate_localization_upsert_is_idempotent(db_session: Session) -> None:
    """### Re-persisting one localization does not duplicate it."""
    canonical = document()
    document_id = upsert_document(db_session, source_id=source_id(db_session), document=canonical, now=timestamp())
    for _ in range(2):
        for payload in localization_payloads(canonical):
            upsert_localization(db_session, document_id=document_id, payload=payload, now=timestamp())
    db_session.flush()
    assert db_session.execute(select(func.count()).select_from(DocumentLocalization)).scalar_one() == 1


def test_resources_keep_metadata_and_survive_failures(db_session: Session) -> None:
    """### Multiple attachments stay separate, including URL-less ones."""
    canonical = document(
        resources=(
            resource(title="a.pdf"),
            resource(
                title="b.xlsx",
                url=f"{SITE}/files/b.xlsx",
                kind="excel",
                status="not_applicable",
                mime_type=None,
                file_size=None,
            ),
            resource(title="c.pdf", status="url_unavailable", error="URL not exposed"),
        )
    )
    document_id = upsert_document(db_session, source_id=source_id(db_session), document=canonical, now=timestamp())
    for resource_row in canonical.resources:
        upsert_resource(db_session, document_id=document_id, resource=resource_row, now=timestamp())
    db_session.flush()

    rows = db_session.execute(select(DocumentResource).order_by(DocumentResource.title)).scalars().all()
    assert [row.title for row in rows] == ["a.pdf", "b.xlsx", "c.pdf"]
    assert rows[1].resource_type == "excel"
    assert rows[1].extraction_status == "not_applicable"
    assert rows[2].original_url is None and rows[2].extraction_status == "url_unavailable"


def test_failed_resource_refresh_preserves_extracted_text(db_session: Session) -> None:
    """### A later extraction failure does not erase the stored PDF text."""
    canonical = document(resources=(resource(),))
    document_id = upsert_document(db_session, source_id=source_id(db_session), document=canonical, now=timestamp())
    upsert_resource(db_session, document_id=document_id, resource=canonical.resources[0], now=timestamp())
    db_session.flush()
    stored = db_session.execute(select(DocumentResource)).scalar_one()
    original_text = stored.extracted_text
    original_hash = stored.content_hash

    failing = document(resources=(resource(status="failed", text=None, error="pdf could not be parsed"),)).resources[0]
    upsert_resource(db_session, document_id=document_id, resource=failing, now=timestamp() + timedelta(hours=1))
    db_session.flush()
    db_session.expire_all()
    refreshed = db_session.execute(select(DocumentResource)).scalar_one()
    assert refreshed.extraction_status == "failed"
    assert refreshed.extraction_error == "pdf could not be parsed"
    assert refreshed.extracted_text == original_text
    assert refreshed.content_hash == original_hash


def test_resource_hash_does_not_determine_identity(db_session: Session) -> None:
    """### Identical extracted text with different URLs stays two resources."""
    canonical = document(
        resources=(
            resource(title="a.pdf", url=f"{SITE}/a.pdf"),
            resource(title="b.pdf", url=f"{SITE}/b.pdf"),
        )
    )
    document_id = upsert_document(db_session, source_id=source_id(db_session), document=canonical, now=timestamp())
    for resource_row in canonical.resources:
        upsert_resource(db_session, document_id=document_id, resource=resource_row, now=timestamp())
    db_session.flush()
    assert db_session.execute(select(func.count()).select_from(DocumentResource)).scalar_one() == 2


def test_report_persists_three_sources_with_independent_statuses(session_factory: sessionmaker[Session]) -> None:
    """### Each source keeps its own outcome in one run."""
    report = CrawlRunReport(
        results=[
            result(
                source="uae_legislation_news",
                documents=(document(),),
                detail_pages_attempted=1,
                detail_pages_succeeded=1,
            ),
            result(
                source="cba_news_releases",
                documents=(document(source="cba_news_releases", url=f"{SITE}/article/one"),),
                detail_pages_attempted=2,
                detail_pages_succeeded=1,
            ),
            result(
                source="commerce_reports", errors=("api fetch failed",), detail_pages_attempted=1, detail_pages_failed=1
            ),
        ],
        duration_seconds=2.0,
    )
    persisted = persist_crawl_report(session_factory, report)
    assert persisted.status == "partial_failure"

    with session_factory() as session:
        run = session.execute(select(CrawlRun)).scalar_one()
        assert run.status == "partial_failure"
        assert run.finished_at is not None and run.duration_ms is not None
        outcomes = session.execute(
            select(CrawlRunSource.status, CrawlRunSource.documents_processed).order_by(CrawlRunSource.id)
        ).all()
        assert [tuple(row) for row in outcomes] == [("success", 1), ("success", 1), ("failed", 0)]


def test_failed_source_persistence_is_isolated(
    session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    """### One source's persistence failure leaves sibling data committed."""
    import lexcrawl.persistence as persistence

    original = persistence.persist_source_result

    def flaky(
        session: Session,
        *,
        crawl_run_id: int,
        result: ActorRunResult,
        now: datetime | None = None,
    ) -> object:
        """### Fail persistence for one source only."""
        if result.source == "cba_news_releases":
            raise RuntimeError("simulated write failure")
        return original(session, crawl_run_id=crawl_run_id, result=result, now=now)

    monkeypatch.setattr(persistence, "persist_source_result", flaky)
    report = CrawlRunReport(
        results=[
            result(
                source="uae_legislation_news",
                documents=(document(),),
                detail_pages_attempted=1,
                detail_pages_succeeded=1,
            ),
            result(
                source="cba_news_releases", documents=(document(source="cba_news_releases", url=f"{SITE}/article/one"),)
            ),
        ]
    )
    persisted = persist_crawl_report(session_factory, report)
    assert persisted.status == "partial_failure"

    with session_factory() as session:
        assert session.execute(select(func.count()).select_from(Document)).scalar_one() == 1
        outcomes = session.execute(select(CrawlRunSource.status).order_by(CrawlRunSource.id)).scalars().all()
        assert outcomes == ["success", "failed"]
        failed = session.execute(select(CrawlRunSource).where(CrawlRunSource.status == "failed")).scalar_one()
        assert failed.error_summary is not None and "simulated write failure" in failed.error_summary
        assert failed.documents_processed == 0


def test_unregistered_source_aborts_before_writing(session_factory: sessionmaker[Session]) -> None:
    """### A run with an unknown source is refused instead of losing that source."""
    report = CrawlRunReport(results=[result(source="unknown_source", documents=(document(source="unknown_source"),))])
    with pytest.raises(PersistenceError, match="not registered"):
        persist_crawl_report(session_factory, report)
    with session_factory() as session:
        assert session.execute(select(func.count()).select_from(CrawlRun)).scalar_one() == 0


def test_counters_and_error_attribution(session_factory: sessionmaker[Session]) -> None:
    """### Counters keep their units and errors keep their attribution."""
    canonical = document(
        resources=(resource(status="failed", text=None, error="pdf failed"),),
    )
    report = CrawlRunReport(
        results=[
            result(
                source="uae_legislation_news",
                documents=(canonical,),
                errors=("article fetch failed at https://x.test/a?api_key=SECRET",),
                listing_pages_attempted=3,
                listing_pages_succeeded=2,
                listing_pages_failed=1,
                detail_pages_attempted=2,
                detail_pages_succeeded=1,
                detail_pages_failed=1,
                resources_processed=1,
                resources_failed=1,
            )
        ]
    )
    persist_crawl_report(session_factory, report)

    with session_factory() as session:
        run_source = session.execute(select(CrawlRunSource)).scalar_one()
        assert run_source.listing_pages_attempted == 3
        assert run_source.detail_pages_failed == 1
        assert run_source.records_without_text == 0
        assert run_source.documents_processed == 1
        assert run_source.documents_failed == 1
        assert run_source.pagination_complete is True

        errors = session.execute(select(CrawlError).order_by(CrawlError.id)).scalars().all()
        assert {error.error_type for error in errors} == {"fetch_error", "resource_extraction_error"}
        for error in errors:
            references = (error.document_id, error.localization_id, error.resource_id)
            assert sum(reference is not None for reference in references) <= 1
        resource_error = next(error for error in errors if error.error_type == "resource_extraction_error")
        assert resource_error.resource_id is not None
        assert all("SECRET" not in error.message for error in errors)
        assert "api_key=***" in next(error.message for error in errors if error.error_type == "fetch_error")


def test_repeated_source_persistence_is_idempotent(session_factory: sessionmaker[Session]) -> None:
    """### Re-persisting the same source result replaces, never duplicates."""
    canonical = document()
    payload = result(
        source="uae_legislation_news",
        documents=(canonical,),
        errors=("boom",),
        detail_pages_attempted=1,
        detail_pages_succeeded=1,
    )
    with session_factory() as session:
        run_pk = new_run(session)
        session.commit()
    for _ in range(2):
        with session_factory() as session:
            persist_source_result(session, crawl_run_id=run_pk, result=payload)
            session.commit()

    with session_factory() as session:
        assert session.execute(select(func.count()).select_from(CrawlRunSource)).scalar_one() == 1
        assert session.execute(select(func.count()).select_from(Document)).scalar_one() == 1
        assert session.execute(select(func.count()).select_from(CrawlError)).scalar_one() == 1


def test_concurrent_upserts_create_one_document(session_factory: sessionmaker[Session]) -> None:
    """### Two concurrent writers converge on one document row."""
    barrier = threading.Barrier(2)
    failures: list[BaseException] = []

    def write() -> None:
        """### Upsert the same document inside its own transaction."""
        try:
            with session_factory() as session:
                barrier.wait(timeout=10)
                upsert_document(
                    session,
                    source_id=source_id(session),
                    document=document(),
                    now=timestamp(),
                )
                session.commit()
        except BaseException as error:  # $ Collected so the assertion reports the real failure.$
            failures.append(error)

    threads = [threading.Thread(target=write) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)

    assert failures == []
    with session_factory() as session:
        assert session.execute(select(func.count()).select_from(Document)).scalar_one() == 1


def test_persistence_requires_a_registered_source(session_factory: sessionmaker[Session]) -> None:
    """### An unregistered source fails loudly instead of writing partial data."""
    with session_factory() as session:
        run_pk = new_run(session)
        session.commit()
        assert source_id_for(session, "unknown_source") is None
    with pytest.raises(PersistenceError, match="not registered"), session_factory() as session:
        persist_source_result(
            session,
            crawl_run_id=run_pk,
            result=result(source="unknown_source", documents=(document(source="unknown_source"),)),
        )


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        ([], "failed"),
        (["success", "success"], "success"),
        (["success", "failed"], "partial_failure"),
        (["partial_failure", "failed"], "partial_failure"),
        (["failed", "failed"], "failed"),
    ],
)
def test_aggregate_status(statuses: list[str], expected: str) -> None:
    """### Run status aggregates the persisted source outcomes."""
    outcomes = [
        PersistedSourceRun(crawl_run_id=1, source_code=f"s{index}", status=status, documents=0, errors=0)
        for index, status in enumerate(statuses)
    ]
    assert aggregate_status(outcomes) == expected


def test_redact_secrets_removes_credentials_and_keys() -> None:
    """### Messages never retain passwords or API keys."""
    message = "failed postgresql+psycopg://user:password@host/db and https://x.test/a?api_key=ABC123&token=T1"
    redacted = redact_secrets(message)
    assert "password" not in redacted
    assert "ABC123" not in redacted
    assert "T1" not in redacted
    assert "api_key=***" in redacted


def test_no_change_run_is_persisted_as_success(session_factory: sessionmaker[Session]) -> None:
    """### A verified no-change run is stored as successful with zero documents."""
    report = CrawlRunReport(results=[result(source="commerce_reports", check_outcome="no_change")])
    persisted = persist_crawl_report(session_factory, report)

    assert persisted.status == "success"
    with session_factory() as session:
        run_source = session.execute(select(CrawlRunSource)).scalar_one()
        assert run_source.status == "success"
        assert run_source.check_outcome == "no_change"
        assert run_source.documents_processed == 0
        assert run_source.pagination_complete is True


def test_budget_accounting_is_persisted(session_factory: sessionmaker[Session]) -> None:
    """### Pending reports and provider credits survive persistence."""
    canonical = document(source="commerce_reports", url=f"{SITE}/reports/2026/01/one", published_at=None)
    report = CrawlRunReport(
        results=[
            result(
                source="commerce_reports",
                documents=(canonical,),
                check_outcome="new_data",
                documents_pending=4,
                provider_credits_used=7,
                detail_pages_attempted=5,
                detail_pages_succeeded=1,
            )
        ]
    )
    persist_crawl_report(session_factory, report)

    with session_factory() as session:
        run_source = session.execute(select(CrawlRunSource)).scalar_one()
        assert run_source.check_outcome == "new_data"
        assert run_source.documents_pending == 4
        assert run_source.provider_credits_used == 7
        assert run_source.documents_processed == 1


def test_incomplete_attempts_accumulate_then_reset(db_session: Session) -> None:
    """### Incomplete documents count attempts; completing resets the counter."""
    source = source_id(db_session, "commerce_reports")
    pending = document(
        source="commerce_reports", url=f"{SITE}/reports/one", text=None, status="failed", published_at=None
    )
    document_id = upsert_document(db_session, source_id=source, document=pending, now=timestamp())
    db_session.flush()
    assert db_session.get(Document, document_id).extraction_attempts == 1

    upsert_document(db_session, source_id=source, document=pending, now=timestamp())
    db_session.flush()
    assert db_session.get(Document, document_id).extraction_attempts == 2

    completed = document(
        source="commerce_reports", url=f"{SITE}/reports/one", text="report body", status="complete", published_at=None
    )
    upsert_document(db_session, source_id=source, document=completed, now=timestamp())
    db_session.flush()
    assert db_session.get(Document, document_id).extraction_attempts == 0


def test_incomplete_reports_query_selects_only_retry_candidates(db_session: Session) -> None:
    """### Only incomplete reports below the attempt budget are retried."""
    source = source_id(db_session, "commerce_reports")
    pending = document(
        source="commerce_reports", url=f"{SITE}/reports/pending", text=None, status="failed", published_at=None
    )
    document_id = upsert_document(db_session, source_id=source, document=pending, now=timestamp())
    for payload in localization_payloads(pending):
        upsert_localization(db_session, document_id=document_id, payload=payload, now=timestamp())
    done = document(
        source="commerce_reports", url=f"{SITE}/reports/done", text="body", status="complete", published_at=None
    )
    upsert_document(db_session, source_id=source, document=done, now=timestamp())
    db_session.flush()

    candidates = incomplete_reports(db_session, source_code="commerce_reports", max_attempts=3)
    assert [candidate.url for candidate in candidates] == [f"{SITE}/reports/pending"]
    assert candidates[0].identity == pending.identity_key
    assert candidates[0].title == pending.title
    assert candidates[0].attempts == 1
    assert incomplete_reports(db_session, source_code="commerce_reports", max_attempts=1) == []
