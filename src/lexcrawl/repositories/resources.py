"""### Idempotent resource reads, atomic claiming, and result writes.

Writes are monotonic per (document, url): a completed attachment is never replaced by
a later failed attempt. Claiming uses `FOR UPDATE SKIP LOCKED` so concurrent workers
cannot select the same row; the documented worker model is a single process per
database, with the file lock preventing overlap on one host.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from lexcrawl.db.models import DocumentResource
from lexcrawl.models.document import DocumentResource as CanonicalResource

COMPLETE = "complete"
RETRYABLE_STATUSES = ("pending", "failed")


@dataclass(frozen=True, slots=True)
class ResourceClaim:
    """### A claimed attachment ready to be fetched outside a transaction."""

    id: int
    url: str
    kind: str


def _find(session: Session, document_id: int, resource: CanonicalResource) -> DocumentResource | None:
    """### Locate an existing row for a resource, keyed by URL (or title when URL-less)."""
    statement = select(DocumentResource).where(DocumentResource.document_id == document_id)
    if resource.url is not None:
        statement = statement.where(DocumentResource.url == resource.url)
    else:
        statement = statement.where(DocumentResource.title == resource.title)
    return session.scalars(statement).first()


def upsert_resource(
    session: Session, *, document_id: int, resource: CanonicalResource, now: datetime
) -> int:
    """### Insert or refresh one attachment, keeping completed content over a failed retry."""
    incoming_complete = resource.status == COMPLETE and resource.text is not None
    existing = _find(session, document_id, resource)
    if existing is None:
        row = DocumentResource(
            document_id=document_id,
            title=resource.title,
            url=resource.url,
            kind=resource.kind,
            status=resource.status,
            text=resource.text,
            content_hash=resource.content_hash,
            error=resource.error,
            attempts=0,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        session.flush()
        return row.id

    if incoming_complete:
        # $ Only a completed extraction may overwrite stored content.$
        existing.text = resource.text
        existing.content_hash = resource.content_hash
        existing.status = COMPLETE
        existing.error = None
    existing.title = resource.title
    existing.kind = resource.kind
    existing.updated_at = now
    session.flush()
    return existing.id


def claim_due_resources(
    session: Session,
    *,
    attempt_cap: int,
    document_ids: Sequence[int],
    now: datetime,
    lease_seconds: int = 300,
    limit: int | None = None,
) -> list[ResourceClaim]:
    """### Atomically claim due PDF attachments, recording a durable lease."""
    if not document_ids:
        return []
    cutoff = now - timedelta(seconds=max(1, lease_seconds))
    statement = (
        select(DocumentResource)
        .where(
            DocumentResource.kind == "pdf",
            DocumentResource.status.in_(RETRYABLE_STATUSES),
            DocumentResource.attempts < attempt_cap,
            DocumentResource.url.is_not(None),
            DocumentResource.document_id.in_(document_ids),
            # $ Skip rows another worker is fetching, unless its lease has expired.$
            or_(DocumentResource.claimed_at.is_(None), DocumentResource.claimed_at < cutoff),
        )
        .order_by(DocumentResource.id)
        .with_for_update(skip_locked=True)
    )
    if limit is not None:
        statement = statement.limit(limit)
    claims: list[ResourceClaim] = []
    for row in session.scalars(statement).all():
        row.attempts = min(row.attempts + 1, 32767)
        row.last_extracted_at = now
        row.claimed_at = now
        row.updated_at = now
        if row.url is not None:
            claims.append(ResourceClaim(id=row.id, url=row.url, kind=row.kind))
    return claims


def mark_resource_extracted(
    session: Session, *, resource_id: int, text: str, content_hash: str, now: datetime
) -> None:
    """### Record a successful extraction."""
    row = session.get(DocumentResource, resource_id)
    if row is None:
        return
    row.status = COMPLETE
    row.text = text
    row.content_hash = content_hash
    row.error = None
    row.claimed_at = None
    row.updated_at = now


def mark_resource_failed(session: Session, *, resource_id: int, error: str, now: datetime) -> None:
    """### Record a failed extraction attempt."""
    row = session.get(DocumentResource, resource_id)
    if row is None:
        return
    row.status = "failed"
    row.error = error
    row.claimed_at = None
    row.updated_at = now


def resources_for(session: Session, *, document_id: int) -> Sequence[DocumentResource]:
    """### Return every attachment belonging to a document."""
    return session.scalars(
        select(DocumentResource).where(DocumentResource.document_id == document_id)
    ).all()
