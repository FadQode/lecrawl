"""### API response contracts, defined before any FastAPI routes.

These mirror the repository read models, add pagination envelopes, and define a
consistent error/job-accepted shape. `from_attributes` lets them validate the
repository dataclasses directly.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class _Base(BaseModel):
    """### Base contract model."""

    model_config = ConfigDict(from_attributes=True)


class DocumentListItemModel(_Base):
    """### Document metadata in list responses (no text)."""

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
    topics: list[str]
    first_seen_at: datetime
    last_seen_at: datetime


class DocumentPageModel(_Base):
    """### One page of documents."""

    items: list[DocumentListItemModel]
    next_cursor: str | None
    has_more: bool


class DocumentDetailModel(DocumentListItemModel):
    """### A single document including extracted text."""

    native_id: str | None
    published_date_raw: str | None
    extraction_error: str | None
    text: str | None


class ResourceModel(_Base):
    """### One attachment."""

    id: int
    document_id: int
    title: str
    url: str | None
    kind: str
    status: str
    extraction_status: str | None
    content_hash: str | None
    error: str | None


class SourceModel(_Base):
    """### A registered source."""

    id: int
    code: str
    name: str
    base_url: str | None
    is_active: bool


class RunListItemModel(_Base):
    """### Crawl-run metadata."""

    id: int
    run_id: str
    trigger_type: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    duration_ms: int | None
    credits_spent: int


class RunPageModel(_Base):
    """### One page of runs."""

    items: list[RunListItemModel]
    next_cursor: str | None
    has_more: bool


class RunSourceModel(_Base):
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


class RunErrorModel(_Base):
    """### One persisted error within a source run."""

    stage: str
    error_type: str
    message: str
    occurred_at: datetime
    document_id: int | None


class RunDetailModel(_Base):
    """### A run with its source metrics and errors."""

    item: RunListItemModel
    sources: list[RunSourceModel]
    errors: list[RunErrorModel]
    errors_total: int = 0


class ErrorEnvelope(BaseModel):
    """### Problem-style error envelope."""

    type: str
    title: str
    status: int
    detail: str | None = None
    instance: str | None = None


class JobRefModel(BaseModel):
    """### One accepted per-source job."""

    source: str
    run_id: str
    status: str
    status_url: str


class JobAccepted(BaseModel):
    """### Response for accepted crawl jobs (202): one job per requested source."""

    jobs: list[JobRefModel]



class DocumentTextModel(BaseModel):
    """### A document's extracted text and hash."""

    document_id: int
    text: str | None
    content_hash: str | None


class CreditUsageModel(BaseModel):
    """### Monthly credits per source."""

    month: datetime
    source: str
    credits_used: int
    executions: int
    avg_credits_per_execution: float | None = None


class PendingDocumentModel(BaseModel):
    """### A document eligible for another processing attempt."""

    source: str
    document_id: int
    identity_key: str
    status: str | None
    attempts: int
    last_error: str | None
    last_seen_at: datetime | None


class JobRequest(BaseModel):
    """### POST /jobs request body."""

    sources: list[str] | None = None
    trigger: Literal["manual", "api"] = "api"
