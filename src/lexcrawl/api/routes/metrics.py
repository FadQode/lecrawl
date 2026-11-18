"""### Prometheus metrics endpoint (unauthenticated, aggregate-only)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from sqlalchemy import Engine

from lexcrawl.api.dependencies import get_engine
from lexcrawl.db.session import create_session_factory, session_scope
from lexcrawl.metrics import METRICS
from lexcrawl.repositories import jobs as jobs_repo

router = APIRouter(tags=["metrics"])

# $ Prometheus scrapes this without credentials, so it exposes aggregate counters only.$
CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"


@router.get("/metrics")
def read_metrics(engine: Engine | None = Depends(get_engine)) -> Response:
    """### Render the metrics registry, refreshing queue gauges from the database."""
    if engine is not None:
        factory = create_session_factory(engine)
        with session_scope(factory) as session:
            counts = jobs_repo.count_by_status(session)
        METRICS.set(
            "lexcrawl_jobs",
            float(counts.get("queued", 0)),
            help_text="Jobs waiting to be claimed.",
            labels={"status": "queued"},
        )
        METRICS.set(
            "lexcrawl_jobs",
            float(counts.get("claimed", 0) + counts.get("running", 0)),
            help_text="Jobs waiting to be claimed.",
            labels={"status": "active"},
        )
    return Response(METRICS.render(), media_type=CONTENT_TYPE)
