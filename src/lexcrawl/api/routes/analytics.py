"""### Analytics endpoints (credits and pending documents)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session, sessionmaker

from lexcrawl.api.auth import require_bearer_token
from lexcrawl.api.contracts import CreditUsageModel, PendingDocumentModel
from lexcrawl.api.dependencies import get_factory
from lexcrawl.db.session import session_scope
from lexcrawl.repositories import analytics as analytics_repo

router = APIRouter(prefix="/analytics", tags=["analytics"], dependencies=[Depends(require_bearer_token)])


@router.get("/credits", response_model=list[CreditUsageModel])
def credits(factory: sessionmaker[Session] = Depends(get_factory)) -> list[CreditUsageModel]:
    """### Monthly provider credits per source (from run records)."""
    with session_scope(factory) as session:
        rows = analytics_repo.monthly_credit_usage(session)
    return [CreditUsageModel.model_validate(dict(row)) for row in rows]


@router.get("/pending", response_model=list[PendingDocumentModel])
def pending(
    limit: int = Query(default=100, ge=1, le=500),
    factory: sessionmaker[Session] = Depends(get_factory),
) -> list[PendingDocumentModel]:
    """### Documents still eligible for another processing attempt."""
    with session_scope(factory) as session:
        rows = analytics_repo.pending_documents(session, limit=limit)
    return [PendingDocumentModel.model_validate(dict(row)) for row in rows]
