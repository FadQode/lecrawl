"""### Response schemas for crawl runs, per-source results, and errors."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from lexcrawl.db.models import CrawlError, CrawlRun, CrawlRunSource


class CrawlRequest(BaseModel):
    """### Request body to trigger a crawl."""

    model_config = ConfigDict(json_schema_extra={"example": {"source": "cba", "max_pages": 2}})

    source: Literal["uae", "commerce", "cba", "all"] = Field(
        description="Source to crawl: 'uae', 'cba', 'commerce', or 'all'."
    )
    max_pages: int | None = Field(
        default=None,
        ge=1,
        le=1000,
        description="Maximum listing pages per source; omit to use the configured default.",
    )


class CrawlAccepted(BaseModel):
    """### Response returned when a crawl is accepted for background execution."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": "0deae9b4-1111-2222-3333-444455556666",
                "status": "running",
                "trigger_type": "api",
                "message": "Crawl accepted.",
            }
        }
    )

    id: UUID = Field(description="Crawl run identifier; poll GET /crawl-runs/{id} for status.")
    status: str = Field(description="Initial run status; always 'running' when accepted.")
    trigger_type: str = Field(description="How the run was triggered; 'api' for this endpoint.")
    message: str = Field(description="Human-readable acknowledgement.")


class RunSourceOut(BaseModel):
    """### One source's outcome inside a crawl run."""

    source: str = Field(description="Source code.")
    source_name: str | None = Field(default=None, description="Human-readable source name.")
    status: str = Field(description="Per-source status: success, partial_failure, or failed.")
    check_outcome: str | None = Field(
        default=None, description="Preflight outcome: no_change, new_data, or check_failed."
    )
    pagination_complete: bool = Field(description="Whether listing pagination finished without truncation.")
    articles_discovered: int = Field(description="Articles discovered from the listing.")
    detail_pages_succeeded: int = Field(description="Detail pages fetched successfully.")
    detail_pages_failed: int = Field(description="Detail pages that failed.")
    documents_processed: int = Field(description="Documents emitted for this source.")
    documents_failed: int = Field(description="Documents that failed extraction.")
    documents_pending: int = Field(description="Documents still pending extraction.")
    resources_extracted: int = Field(description="Resources extracted successfully.")
    resources_failed: int = Field(description="Resources that failed or were blocked.")
    provider_credits_used: int = Field(description="Third-party provider credits consumed.")
    error_summary: str | None = Field(default=None, description="Bounded summary of the run's errors.")
    started_at: datetime = Field(description="When this source run started.")
    finished_at: datetime | None = Field(default=None, description="When this source run finished.")

    @classmethod
    def from_model(cls, run_source: CrawlRunSource) -> RunSourceOut:
        """### Map a per-source run row to its public representation."""
        source = run_source.source
        return cls(
            source=source.code,
            source_name=source.name,
            status=run_source.status,
            check_outcome=run_source.check_outcome,
            pagination_complete=run_source.pagination_complete,
            articles_discovered=run_source.articles_discovered,
            detail_pages_succeeded=run_source.detail_pages_succeeded,
            detail_pages_failed=run_source.detail_pages_failed,
            documents_processed=run_source.documents_processed,
            documents_failed=run_source.documents_failed,
            documents_pending=run_source.documents_pending,
            resources_extracted=run_source.resources_extracted,
            resources_failed=run_source.resources_failed,
            provider_credits_used=run_source.provider_credits_used,
            error_summary=run_source.error_summary,
            started_at=run_source.started_at,
            finished_at=run_source.finished_at,
        )


class RunOut(BaseModel):
    """### Public representation of a crawl run and its per-source results."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "run_id": "0deae9b4-1111-2222-3333-444455556666",
                "trigger_type": "manual",
                "status": "partial_failure",
                "started_at": "2025-11-05T08:00:00Z",
                "finished_at": "2025-11-05T08:00:11Z",
                "duration_ms": 11000,
                "sources": [],
            }
        }
    )

    run_id: UUID = Field(description="Unique crawl run identifier (UUID).")
    trigger_type: str = Field(description="How the run was triggered: manual or scheduled.")
    status: str = Field(description="Run status: running, success, partial_failure, or failed.")
    started_at: datetime = Field(description="When the run started.")
    finished_at: datetime | None = Field(default=None, description="When the run finished.")
    duration_ms: int | None = Field(default=None, description="Run duration in milliseconds.")
    sources: list[RunSourceOut] = Field(description="Per-source results for this run.")

    @classmethod
    def from_model(cls, run: CrawlRun) -> RunOut:
        """### Map a crawl run row to its public representation."""
        ordered = sorted(run.run_sources, key=lambda item: item.id)
        return cls(
            run_id=run.run_id,
            trigger_type=run.trigger_type,
            status=run.status,
            started_at=run.started_at,
            finished_at=run.finished_at,
            duration_ms=run.duration_ms,
            sources=[RunSourceOut.from_model(item) for item in ordered],
        )


class CrawlErrorOut(BaseModel):
    """### Public representation of one structured crawl error."""

    id: int = Field(description="Error identifier.")
    stage: str = Field(
        description="Failure stage: discovery, fetch, parse, validate, extract, persistence, or unknown."
    )
    error_type: str = Field(description="Error category, e.g. fetch_error, parse_error, resource_error.")
    message: str = Field(description="Human-readable, redacted error message.")
    retryable: bool | None = Field(default=None, description="Whether the error is considered retryable, when known.")
    occurred_at: datetime = Field(description="When the error occurred.")
    document_id: int | None = Field(default=None, description="Attributed document id, if any.")
    localization_id: int | None = Field(default=None, description="Attributed localization id, if any.")
    resource_id: int | None = Field(default=None, description="Attributed resource id, if any.")

    @classmethod
    def from_model(cls, error: CrawlError) -> CrawlErrorOut:
        """### Map a crawl error row to its public representation."""
        return cls(
            id=error.id,
            stage=error.stage,
            error_type=error.error_type,
            message=error.message,
            retryable=error.retryable,
            occurred_at=error.occurred_at,
            document_id=error.document_id,
            localization_id=error.localization_id,
            resource_id=error.resource_id,
        )
