"""### Source listing endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, sessionmaker

from lexcrawl.api.auth import require_bearer_token
from lexcrawl.api.contracts import SourceModel
from lexcrawl.api.dependencies import get_factory
from lexcrawl.db.session import session_scope
from lexcrawl.repositories import reads

router = APIRouter(prefix="/sources", tags=["sources"], dependencies=[Depends(require_bearer_token)])


@router.get("", response_model=list[SourceModel])
def list_sources(factory: sessionmaker[Session] = Depends(get_factory)) -> list[SourceModel]:
    """### List registered sources."""
    with session_scope(factory) as session:
        return [SourceModel.model_validate(item) for item in reads.list_sources(session)]


@router.get("/{code}", response_model=SourceModel)
def get_source(code: str, factory: sessionmaker[Session] = Depends(get_factory)) -> SourceModel:
    """### Return one source by code."""
    with session_scope(factory) as session:
        found = reads.get_source(session, code)
    if found is None:
        raise HTTPException(status_code=404, detail="source not found")
    return SourceModel.model_validate(found)
