"""### Bounded, keyset-paginated read queries for API use.

Lists select only metadata (never the large `text` column); `get_document` is the
explicit path that returns full extracted text. Cursors are filter-bound and validated.
"""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import and_, func, nulls_last, or_, select
from sqlalchemy.orm import Session

from lexcrawl.db.models import CrawlError, CrawlRun, CrawlRunSource, Document, DocumentResource, Source

MAX_PAGE_SIZE = 200
RUN_FILTER_TOKEN = "runs"


class InvalidCursorError(ValueError):
    """### Raised when a cursor is malformed or used with different filters."""


@dataclass(frozen=True, slots=True)
class DocumentFilters:
    """### Filters for listing documents."""

    source_code: str | None = None
    extraction_status: str | None = None
    document_type: str | None = None
    topic: str | None = None
    published_from: date | None = None
    published_to: date | None = None


@dataclass(frozen=True, slots=True)
class DocumentListItem:
    """### Document metadata (no extracted text)."""

    id: int
    source_code: str
    identity_key: str
    source_url: str
    title: str
    language: str
    document_type: str | None
    summary: str | None
    image_url: str | None
    published_at: date | None
    published_year: int | None
    published_month: int | None
    published_precision: str
    extraction_status: str | None
    content_hash: str | None
    topics: tuple[str, ...]
    first_seen_at: datetime
    last_seen_at: datetime


@dataclass(frozen=True, slots=True)
class DocumentDetail:
    """### A single document including its extracted text."""

    id: int
    source_code: str
    native_id: str | None
    identity_key: str
    source_url: str
    title: str
    language: str
    document_type: str | None
    summary: str | None
    image_url: str | None
    published_at: date | None
    published_month: int | None
    published_year: int | None
    published_date_raw: str | None
    published_precision: str
    extraction_status: str | None
    extraction_error: str | None
    text: str | None
    content_hash: str | None
    topics: tuple[str, ...]
    first_seen_at: datetime
    last_seen_at: datetime


@dataclass(frozen=True, slots=True)
class DocumentPage:
    """### One page of documents plus the next cursor."""

    items: list[DocumentListItem]
    next_cursor: str | None
    has_more: bool


@dataclass(frozen=True, slots=True)
class ResourceView:
    """### One attachment of a document."""

    id: int
    document_id: int
    title: str
    url: str | None
    kind: str
    status: str
    extraction_status: str | None
    content_hash: str | None
    error: str | None


@dataclass(frozen=True, slots=True)
class SourceView:
    """### A registered source."""

    id: int
    code: str
    name: str
    base_url: str | None
    is_active: bool


@dataclass(frozen=True, slots=True)
class RunListItem:
    """### Crawl-run metadata."""

    id: int
    run_id: str
    trigger_type: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    duration_ms: int | None
    credits_spent: int


@dataclass(frozen=True, slots=True)
class RunPage:
    """### One page of runs plus the next cursor."""

    items: list[RunListItem]
    next_cursor: str | None
    has_more: bool


@dataclass(frozen=True, slots=True)
class RunSourceView:
    """### Per-source metrics within a run."""

    source_code: str
    status: str
    check_outcome: str
    discovered_count: int
    inserted_count: int
    updated_count: int
    skipped_count: int
    failed_count: int
    resource_count: int
    pending_count: int
    credits_used: int
    pagination_complete: bool
    metrics_known: bool
    detail: str | None
    started_at: datetime | None
    finished_at: datetime | None


@dataclass(frozen=True, slots=True)
class RunErrorView:
    """### One persisted error within a source run."""

    stage: str
    error_type: str
    message: str
    occurred_at: datetime
    document_id: int | None


@dataclass(frozen=True, slots=True)
class RunDetail:
    """### A run with its source metrics and errors."""

    item: RunListItem
    sources: list[RunSourceView] = field(default_factory=list)
    errors: list[RunErrorView] = field(default_factory=list)
    errors_total: int = 0


def _bounded(limit: int) -> int:
    """### Clamp a requested page size to 1..MAX_PAGE_SIZE."""
    return max(1, min(limit, MAX_PAGE_SIZE))


def _document_filter_hash(filters: DocumentFilters) -> str:
    """### Stable hash describing a filter set, bound into the cursor."""
    raw = "|".join(
        str(value)
        for value in (
            filters.source_code,
            filters.extraction_status,
            filters.document_type,
            filters.topic,
            filters.published_from,
            filters.published_to,
        )
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _encode_cursor(payload: dict[str, Any]) -> str:
    """### Encode a cursor payload as URL-safe base64 JSON."""
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def _decode_cursor(cursor: str) -> dict[str, Any]:
    """### Decode a cursor, rejecting malformed input."""
    try:
        payload = json.loads(base64.urlsafe_b64decode(cursor.encode("ascii")))
    except (ValueError, TypeError, json.JSONDecodeError) as error:
        raise InvalidCursorError("malformed cursor") from error
    if not isinstance(payload, dict):
        raise InvalidCursorError("malformed cursor")
    return payload


def _document_order() -> tuple[Any, ...]:
    """### Keyset order: published_at DESC NULLS LAST, then id DESC."""
    return (nulls_last(Document.published_at.desc()), Document.id.desc())


def _item_from_row(row: Any) -> DocumentListItem:
    """### Map a row mapping to a document list item."""
    return DocumentListItem(
        id=row["id"],
        source_code=row["source_code"],
        identity_key=row["identity_key"],
        source_url=row["source_url"],
        title=row["title"],
        language=row["language"],
        document_type=row["document_type"],
        summary=row["summary"],
        image_url=row["image_url"],
        published_at=row["published_at"],
        published_year=row["published_year"],
        published_month=row["published_month"],
        published_precision=row["published_precision"],
        extraction_status=row["extraction_status"],
        content_hash=row["content_hash"],
        topics=tuple(row["topics"] or ()),
        first_seen_at=row["first_seen_at"],
        last_seen_at=row["last_seen_at"],
    )


def list_documents(
    session: Session,
    filters: DocumentFilters,
    *,
    cursor: str | None = None,
    limit: int = 50,
) -> DocumentPage:
    """### List documents with bounded, filter-bound keyset pagination."""
    bounded = _bounded(limit)
    filter_hash = _document_filter_hash(filters)
    statement = select(
        Document.id,
        Source.code.label("source_code"),
        Document.identity_key,
        Document.source_url,
        Document.title,
        Document.language,
        Document.document_type,
        Document.summary,
        Document.image_url,
        Document.published_at,
        Document.published_year,
        Document.published_month,
        Document.published_precision,
        Document.extraction_status,
        Document.content_hash,
        Document.topics,
        Document.first_seen_at,
        Document.last_seen_at,
    ).join(Source, Source.id == Document.source_id)

    if filters.source_code:
        statement = statement.where(Source.code == filters.source_code)
    if filters.extraction_status:
        statement = statement.where(Document.extraction_status == filters.extraction_status)
    if filters.document_type:
        statement = statement.where(Document.document_type == filters.document_type)
    if filters.topic:
        statement = statement.where(Document.topics.contains([filters.topic]))
    if filters.published_from:
        statement = statement.where(Document.published_at >= filters.published_from)
    if filters.published_to:
        statement = statement.where(Document.published_at <= filters.published_to)

    if cursor:
        payload = _decode_cursor(cursor)
        if payload.get("f") != filter_hash:
            raise InvalidCursorError("cursor does not match the requested filters")
        raw_id = payload.get("i")
        if not isinstance(raw_id, int):
            raise InvalidCursorError("malformed cursor")
        raw_published = payload.get("p")
        if raw_published is None:
            statement = statement.where(
                and_(Document.published_at.is_(None), Document.id < raw_id)
            )
        else:
            try:
                published = datetime.fromisoformat(str(raw_published)).date()
            except ValueError as error:
                raise InvalidCursorError("malformed cursor") from error
            statement = statement.where(
                or_(
                    Document.published_at.is_(None),
                    Document.published_at < published,
                    and_(Document.published_at == published, Document.id < raw_id),
                )
            )

    statement = statement.order_by(*_document_order()).limit(bounded + 1)
    rows = session.execute(statement).mappings().all()
    has_more = len(rows) > bounded
    page_rows = rows[:bounded]
    items = [_item_from_row(row) for row in page_rows]
    next_cursor = None
    if has_more and items:
        last = items[-1]
        next_cursor = _encode_cursor(
            {
                "p": last.published_at.isoformat() if last.published_at else None,
                "i": last.id,
                "f": filter_hash,
            }
        )
    return DocumentPage(items=items, next_cursor=next_cursor, has_more=has_more)


def get_document(session: Session, document_id: int) -> DocumentDetail | None:
    """### Return a single document including its extracted text."""
    row = session.execute(
        select(Document, Source.code.label("source_code"))
        .join(Source, Source.id == Document.source_id)
        .where(Document.id == document_id)
    ).first()
    if row is None:
        return None
    document, source_code = row
    return DocumentDetail(
        id=document.id,
        source_code=source_code,
        native_id=document.native_id,
        identity_key=document.identity_key,
        source_url=document.source_url,
        title=document.title,
        language=document.language,
        document_type=document.document_type,
        summary=document.summary,
        image_url=document.image_url,
        published_at=document.published_at,
        published_month=document.published_month,
        published_year=document.published_year,
        published_date_raw=document.published_date_raw,
        published_precision=document.published_precision,
        extraction_status=document.extraction_status,
        extraction_error=document.extraction_error,
        text=document.text,
        content_hash=document.content_hash,
        topics=tuple(document.topics or ()),
        first_seen_at=document.first_seen_at,
        last_seen_at=document.last_seen_at,
    )


def list_resources(session: Session, document_id: int) -> list[ResourceView]:
    """### Return the attachments of a document."""
    rows = session.scalars(
        select(DocumentResource).where(DocumentResource.document_id == document_id).order_by(DocumentResource.id)
    ).all()
    return [
        ResourceView(
            id=row.id,
            document_id=row.document_id,
            title=row.title,
            url=row.url,
            kind=row.kind,
            status=row.status,
            extraction_status=row.status,
            content_hash=row.content_hash,
            error=row.error,
        )
        for row in rows
    ]


def list_sources(session: Session) -> list[SourceView]:
    """### Return every registered source."""
    rows = session.scalars(select(Source).order_by(Source.code)).all()
    return [
        SourceView(id=row.id, code=row.code, name=row.name, base_url=row.base_url, is_active=row.is_active)
        for row in rows
    ]


def get_source(session: Session, code: str) -> SourceView | None:
    """### Return one source by code."""
    row = session.scalar(select(Source).where(Source.code == code))
    if row is None:
        return None
    return SourceView(id=row.id, code=row.code, name=row.name, base_url=row.base_url, is_active=row.is_active)


def _run_item(row: CrawlRun) -> RunListItem:
    """### Map a run row to a list item."""
    return RunListItem(
        id=row.id,
        run_id=str(row.run_id),
        trigger_type=row.trigger_type,
        status=row.status,
        started_at=row.started_at,
        finished_at=row.finished_at,
        duration_ms=row.duration_ms,
        credits_spent=row.credits_spent,
    )


def list_runs(session: Session, *, cursor: str | None = None, limit: int = 50) -> RunPage:
    """### List runs with bounded keyset pagination (started_at DESC, id DESC)."""
    bounded = _bounded(limit)
    statement = select(CrawlRun).order_by(CrawlRun.started_at.desc(), CrawlRun.id.desc())
    if cursor:
        payload = _decode_cursor(cursor)
        if payload.get("f") != RUN_FILTER_TOKEN:
            raise InvalidCursorError("cursor does not match the requested filters")
        raw_id = payload.get("i")
        raw_started = payload.get("s")
        if not isinstance(raw_id, int) or not isinstance(raw_started, str):
            raise InvalidCursorError("malformed cursor")
        try:
            started = datetime.fromisoformat(raw_started)
        except ValueError as error:
            raise InvalidCursorError("malformed cursor") from error
        statement = statement.where(
            or_(
                CrawlRun.started_at < started,
                and_(CrawlRun.started_at == started, CrawlRun.id < raw_id),
            )
        )
    rows = session.scalars(statement.limit(bounded + 1)).all()
    has_more = len(rows) > bounded
    page_rows = rows[:bounded]
    items = [_run_item(row) for row in page_rows]
    next_cursor = None
    if has_more and page_rows:
        last = page_rows[-1]
        next_cursor = _encode_cursor({"s": last.started_at.isoformat(), "i": last.id, "f": RUN_FILTER_TOKEN})
    return RunPage(items=items, next_cursor=next_cursor, has_more=has_more)


def get_run(session: Session, run_id: str, *, error_limit: int = 100) -> RunDetail | None:
    """### Return a run with its per-source metrics and a bounded slice of errors."""
    try:
        parsed = UUID(run_id)
    except ValueError:
        return None
    run = session.scalar(select(CrawlRun).where(CrawlRun.run_id == parsed))
    if run is None:
        return None
    source_rows = session.execute(
        select(CrawlRunSource, Source.code.label("source_code"))
        .join(Source, Source.id == CrawlRunSource.source_id)
        .where(CrawlRunSource.crawl_run_id == run.id)
    ).all()
    sources = [
        RunSourceView(
            source_code=source_code,
            status=source.status,
            check_outcome=source.check_outcome,
            discovered_count=source.discovered_count,
            inserted_count=source.inserted_count,
            updated_count=source.updated_count,
            skipped_count=source.skipped_count,
            failed_count=source.failed_count,
            resource_count=source.resource_count,
            pending_count=source.pending_count,
            credits_used=source.credits_used,
            pagination_complete=source.pagination_complete,
            metrics_known=source.metrics_known,
            detail=source.detail,
            started_at=source.started_at,
            finished_at=source.finished_at,
        )
        for source, source_code in source_rows
    ]
    bounded = max(1, min(error_limit, 500))
    error_filter = CrawlRunSource.crawl_run_id == run.id
    errors_total = int(
        session.scalar(
            select(func.count())
            .select_from(CrawlError)
            .join(CrawlRunSource, CrawlRunSource.id == CrawlError.crawl_run_source_id)
            .where(error_filter)
        )
        or 0
    )
    error_rows = session.scalars(
        select(CrawlError)
        .join(CrawlRunSource, CrawlRunSource.id == CrawlError.crawl_run_source_id)
        .where(error_filter)
        .order_by(CrawlError.occurred_at)
        .limit(bounded)
    ).all()
    errors = [
        RunErrorView(
            stage=error.stage,
            error_type=error.error_type,
            message=error.message,
            occurred_at=error.occurred_at,
            document_id=error.document_id,
        )
        for error in error_rows
    ]
    return RunDetail(item=_run_item(run), sources=sources, errors=errors, errors_total=errors_total)
