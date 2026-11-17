"""### Crawl-run query endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, sessionmaker

from lexcrawl.api.auth import require_bearer_token
from lexcrawl.api.contracts import (
    RunDetailModel,
    RunErrorModel,
    RunListItemModel,
    RunPageModel,
    RunSourceModel,
)
from lexcrawl.api.dependencies import get_factory
from lexcrawl.db.session import session_scope
from lexcrawl.repositories import reads

router = APIRouter(prefix="/runs", tags=["runs"], dependencies=[Depends(require_bearer_token)])


@router.get("", response_model=RunPageModel)
def list_runs(
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    factory: sessionmaker[Session] = Depends(get_factory),
) -> RunPageModel:
    """### List crawl runs with keyset pagination."""
    with session_scope(factory) as session:
        try:
            page = reads.list_runs(session, cursor=cursor, limit=limit)
        except reads.InvalidCursorError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
    return RunPageModel(
        items=[RunListItemModel.model_validate(item) for item in page.items],
        next_cursor=page.next_cursor,
        has_more=page.has_more,
    )


@router.get("/{run_id}", response_model=RunDetailModel)
def get_run(
    run_id: str,
    error_limit: int = Query(default=100, ge=1, le=500),
    factory: sessionmaker[Session] = Depends(get_factory),
) -> RunDetailModel:
    """### Return a run with per-source metrics and a bounded slice of errors."""
    with session_scope(factory) as session:
        detail = reads.get_run(session, run_id, error_limit=error_limit)
    if detail is None:
        raise HTTPException(status_code=404, detail="run not found")
    return RunDetailModel(
        item=RunListItemModel.model_validate(detail.item),
        sources=[RunSourceModel.model_validate(item) for item in detail.sources],
        errors=[RunErrorModel.model_validate(item) for item in detail.errors],
        errors_total=detail.errors_total,
    )
