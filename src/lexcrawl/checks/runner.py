"""### The daily run (monitor orchestrator).

Probe each source, decide against the **processed** checkpoint, then crawl only when
needed: new data, pending documents due for retry, or a periodic reconciliation. Every
run is recorded (when a recorder is configured) with per-source metrics, credits, and
sanitized errors, and is finalized even if execution fails.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import Engine

from lexcrawl.alerts import ANOMALY, BUDGET_EXCEEDED, CHECK_FAILED, notify
from lexcrawl.checks.base import CheckOutcome
from lexcrawl.checks.budget import CreditBudget, build_credit_budget
from lexcrawl.checks.change_detection import ChangeDetectionService, Decision
from lexcrawl.checks.checkpoints import CheckpointStore
from lexcrawl.checks.identities import DocumentMemory, RememberResult
from lexcrawl.checks.run_records import RunHandle, RunRecorder
from lexcrawl.checks.usage import current_period
from lexcrawl.config import Settings
from lexcrawl.crawling.base import redact_message
from lexcrawl.crawling.memo import MemoizingScraper
from lexcrawl.factory import (
    CHECK_SOURCES,
    CREDIT_SOURCES,
    build_actor,
    build_check,
    build_http_fetcher,
    build_runtime,
    build_scraper,
)
from lexcrawl.locking import AdvisoryLock, RunInProgressError
from lexcrawl.logging import get_logger

if TYPE_CHECKING:
    from lexcrawl.actors.base import ActorRunResult

LOGGER = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class DailySourceResult:
    """### Outcome, metrics, and timing for one source in a daily run."""

    source: str
    outcome: CheckOutcome
    status: str
    documents: int
    inserted: int
    updated: int
    discovered: int
    skipped_known: int
    failed: int
    resources: int
    pending: int
    credits: int
    duration_seconds: float
    detail: str
    pagination_complete: bool
    errors: tuple[str, ...]
    started_at: datetime
    finished_at: datetime


@dataclass(slots=True)
class _CreditCounter:
    """### Accumulates provider credits spent by one source, even if it later fails."""

    total: int = 0


def run_daily(
    settings: Settings,
    *,
    sources: tuple[str, ...] = CHECK_SOURCES,
    force: bool = False,
    bootstrap: bool = False,
    record: bool = True,
    trigger: str = "manual",
    engine: Engine | None = None,
    run_id: str | None = None,
) -> list[DailySourceResult]:
    """### Probe each source, crawl what needs it, and record the run."""
    runtime = build_runtime(settings, engine=engine)
    advisory = AdvisoryLock(runtime.engine) if (record and runtime.engine is not None) else None
    if advisory is not None and not advisory.acquire():
        runtime.close()
        raise RunInProgressError("another crawl run is active")
    checkpoints = runtime.checkpoints
    memory = runtime.memory
    recorder = runtime.recorder
    budget = build_credit_budget(settings, engine=runtime.engine)
    period = current_period()
    effective_trigger = "bootstrap" if bootstrap else "force" if force else trigger
    started_at = datetime.now(tz=UTC)
    handle = recorder.start(effective_trigger, run_id=run_id) if record else None
    results: list[DailySourceResult] = []
    run_status = "failed"
    try:
        for name in sources:
            result = _safe_run_source(
                name,
                settings,
                checkpoints,
                memory,
                budget,
                period,
                force=force,
                bootstrap=bootstrap,
                record=record,
            )
            results.append(result)
            if record and handle is not None:
                _record_source(recorder, handle, result)
        run_status = _aggregate_status(results)
        return results
    finally:
        if record and handle is not None:
            _finalize(recorder, handle, run_status, results, started_at)
        if advisory is not None:
            advisory.release()
        runtime.close()


def _record_source(recorder: RunRecorder, handle: RunHandle, result: DailySourceResult) -> None:
    """### Persist one source's metrics/errors and log with correlation ids."""
    run_id_internal = handle.run_id_internal
    run_id = handle.run_id
    source_run_id = recorder.record_source(
        run_id_internal,
        result.source,
        outcome=result.outcome,
        status=result.status,
        discovered=result.discovered,
        inserted=result.inserted,
        updated=result.updated,
        skipped=result.skipped_known,
        failed=result.failed,
        resources=result.resources,
        pending=result.pending,
        credits=result.credits,
        pagination_complete=result.pagination_complete,
        detail=result.detail,
        started_at=result.started_at,
        finished_at=result.finished_at,
    )
    if source_run_id is not None and result.errors:
        recorder.record_errors(source_run_id, result.errors)
    LOGGER.info(
        "source_crawl_completed",
        extra={
            "run_id": run_id,
            "crawl_run_source_id": source_run_id,
            "source": result.source,
            "status": result.status,
            "discovered": result.discovered,
            "inserted": result.inserted,
            "updated": result.updated,
            "skipped": result.skipped_known,
            "failed": result.failed,
            "credits_used": result.credits,
            "duration_ms": int(result.duration_seconds * 1000),
        },
    )


def _finalize(
    recorder: RunRecorder,
    handle: RunHandle,
    status: str,
    results: list[DailySourceResult],
    started_at: datetime,
) -> None:
    """### Finalize the run record, even on unexpected failure."""
    try:
        duration_ms = int((datetime.now(tz=UTC) - started_at).total_seconds() * 1000)
        recorder.finish(
            handle.run_id_internal,
            status=status,
            credits=sum(result.credits for result in results),
            duration_ms=duration_ms,
        )
    except Exception:  # $ Never let run finalization mask the original failure.$
        LOGGER.exception("run_finalize_failed")


def _safe_run_source(
    name: str,
    settings: Settings,
    checkpoints: CheckpointStore,
    memory: DocumentMemory,
    budget: CreditBudget,
    period: str,
    *,
    force: bool,
    bootstrap: bool,
    record: bool,
) -> DailySourceResult:
    """### Run one source, isolating any failure from the others."""
    started_at = datetime.now(tz=UTC)
    started_mono = time.monotonic()
    credit_counter = _CreditCounter()
    try:
        return _run_source(
            name,
            settings,
            checkpoints,
            memory,
            budget,
            period,
            credit_counter=credit_counter,
            started_at=started_at,
            started_mono=started_mono,
            force=force,
            bootstrap=bootstrap,
            record=record,
        )
    except Exception as error:  # $ One source must never abort the others.$
        LOGGER.exception("daily_source_crashed", extra={"source": name})
        safe = redact_message(repr(error))
        notify(CHECK_FAILED, "source execution failed", source=name, reason=safe)
        return _result(
            name,
            "failed",
            status="failed",
            started_at=started_at,
            started_mono=started_mono,
            credits=credit_counter.total,
            errors=(safe,),
            detail=f"unhandled error: {safe}",
        )


def _run_source(
    name: str,
    settings: Settings,
    checkpoints: CheckpointStore,
    memory: DocumentMemory,
    budget: CreditBudget,
    period: str,
    credit_counter: _CreditCounter,
    started_at: datetime,
    started_mono: float,
    *,
    force: bool,
    bootstrap: bool,
    record: bool,
) -> DailySourceResult:
    """### Run one source: probe, decide, and crawl when required."""
    bypass = force or bootstrap
    scraper = build_scraper(name, settings)
    memo = MemoizingScraper(scraper) if scraper is not None else None
    http_fetcher = build_http_fetcher(settings)
    try:
        check = build_check(name, settings, scraper=memo, http_fetcher=http_fetcher)
        key = check.name
        probe = check.probe()
        if record and probe.credits_used:
            budget.record(period, probe.credits_used)
            credit_counter.total += probe.credits_used

        detector = ChangeDetectionService(checkpoints, record=record)
        decision = detector.decide(key, probe, force=bypass)
        outcome = decision.outcome

        if outcome == "check_failed":
            notify(CHECK_FAILED, "change check failed", source=key, reason=probe.error)
            return _result(
                key,
                outcome,
                status="failed",
                started_at=started_at,
                started_mono=started_mono,
                credits=probe.credits_used,
                errors=(probe.error,) if probe.error else (),
                detail=probe.summary,
            )

        reconcile_due = _reconcile_due(decision, settings)
        should_run = outcome == "new_data" or memory.due_count(key) > 0 or reconcile_due
        if not should_run:
            return _result(
                key,
                outcome,
                status="success",
                started_at=started_at,
                started_mono=started_mono,
                credits=probe.credits_used,
                detail=probe.summary,
            )

        if _budget_exceeded(name, settings, budget, period, bypass):
            spent = budget.committed(period)
            detail = f"{probe.summary}; spent={spent}/{settings.firecrawl_monthly_credit_cap}"
            notify(
                BUDGET_EXCEEDED,
                "monthly provider credit cap reached; crawl skipped",
                source=key,
                spent=spent,
                cap=settings.firecrawl_monthly_credit_cap,
            )
            return _result(
                key,
                "budget_exceeded",
                status="partial_failure",
                started_at=started_at,
                started_mono=started_mono,
                credits=probe.credits_used,
                errors=(detail,),
                detail=detail,
            )

        known = memory.known(key)
        max_new = _anomaly_limit(settings, known, bypass) if outcome == "new_data" else None
        actor = build_actor(
            name,
            settings,
            scraper=memo,
            http_fetcher=http_fetcher,
            is_known=known.__contains__,
            max_new_items=max_new,
        )
        run = actor.run(http_fetcher)
        if record and run.provider_credits_used:
            budget.record(period, run.provider_credits_used)
            credit_counter.total += run.provider_credits_used
        credits = probe.credits_used + run.provider_credits_used

        if run.anomaly_detected:
            notify(ANOMALY, "unexpected burst of new items; crawl skipped", source=key)
            return _result(
                key,
                "anomaly",
                status="partial_failure",
                started_at=started_at,
                started_mono=started_mono,
                discovered=run.articles_discovered,
                skipped_known=run.records_skipped_known,
                credits=credits,
                pagination_complete=run.pagination_complete,
                errors=tuple(run.errors),
                detail=f"{probe.summary}; run_status={run.status}",
            )

        remembered = RememberResult()
        if record:
            remembered = memory.remember(key, run.documents)
            # $ Advance processed only after a fully successful discovery+persist.$
            ingested = len(run.documents) > 0 or run.records_skipped_known > 0
            fully_ingested = (
                run.documents_pending == 0
                and run.records_without_text == 0
                and run.pagination_complete
            )
            if ingested and fully_ingested and (outcome == "new_data" or reconcile_due):
                detector.mark_processed(key, probe.fingerprint)

        return _result(
            key,
            outcome,
            status=_source_status(outcome, run),
            started_at=started_at,
            started_mono=started_mono,
            documents=len(run.documents),
            inserted=remembered.inserted,
            updated=remembered.updated,
            discovered=run.articles_discovered,
            skipped_known=run.records_skipped_known,
            failed=_failed_count(run),
            resources=remembered.resources,
            pending=run.documents_pending,
            credits=credits,
            pagination_complete=run.pagination_complete,
            errors=tuple(run.errors),
            detail=f"{probe.summary}; run_status={run.status}",
        )
    finally:
        if memo is not None:
            memo.close()
        http_fetcher.close()


def _failed_count(run: ActorRunResult) -> int:
    """### Count failed documents (no text; covers failed detail/missing fields) plus listing pages."""
    return run.records_without_text + run.listing_pages_failed


def _source_status(outcome: CheckOutcome, run: ActorRunResult | None) -> str:
    """### Derive a run-source status.

    A crawl may run under `no_change` (retry or reconciliation), so status is derived
    from the actor result whenever a crawl ran; only a genuinely skipped check is a
    success without a run.
    """
    if outcome == "check_failed":
        return "failed"
    if outcome == "anomaly":
        return "partial_failure"
    if run is None:
        return "success" if outcome == "no_change" else "partial_failure"
    clean = (
        run.pagination_complete
        and run.records_without_text == 0
        and run.records_missing_required_fields == 0
        and run.detail_pages_failed == 0
        and run.listing_pages_failed == 0
    )
    if clean:
        return "success"
    if run.documents or run.records_skipped_known:
        return "partial_failure"
    return "failed"


def _aggregate_status(results: list[DailySourceResult]) -> str:
    """### Aggregate per-source statuses into a run status."""
    if not results:
        return "failed"
    statuses = {result.status for result in results}
    if statuses == {"success"}:
        return "success"
    if "success" in statuses or "partial_failure" in statuses:
        return "partial_failure"
    return "failed"


def _reconcile_due(decision: Decision, settings: Settings) -> bool:
    """### Report whether a periodic reconciliation crawl is due."""
    interval = settings.reconcile_interval_days
    if interval <= 0 or decision.processed_at is None:
        return False
    return datetime.now(tz=UTC) - decision.processed_at >= timedelta(days=interval)


def _budget_exceeded(
    name: str, settings: Settings, budget: CreditBudget, period: str, bypass: bool
) -> bool:
    """### Report whether a credit-bearing source has exhausted its monthly budget."""
    cap = settings.firecrawl_monthly_credit_cap
    return not bypass and name in CREDIT_SOURCES and cap > 0 and budget.committed(period) >= cap


def _anomaly_limit(settings: Settings, known: frozenset[str], bypass: bool) -> int | None:
    """### Apply the anomaly limit only on incremental runs with an existing baseline."""
    if bypass or not known or settings.anomaly_max_new_items <= 0:
        return None
    return settings.anomaly_max_new_items


def _result(
    source: str,
    outcome: CheckOutcome,
    *,
    status: str,
    started_at: datetime,
    started_mono: float,
    documents: int = 0,
    inserted: int = 0,
    updated: int = 0,
    discovered: int = 0,
    skipped_known: int = 0,
    failed: int = 0,
    resources: int = 0,
    pending: int = 0,
    credits: int = 0,
    pagination_complete: bool = False,
    errors: tuple[str, ...] = (),
    detail: str = "",
) -> DailySourceResult:
    """### Assemble a per-source daily result."""
    finished_at = datetime.now(tz=UTC)
    return DailySourceResult(
        source=source,
        outcome=outcome,
        status=status,
        documents=documents,
        inserted=inserted,
        updated=updated,
        discovered=discovered,
        skipped_known=skipped_known,
        failed=failed,
        resources=resources,
        pending=pending,
        credits=credits,
        duration_seconds=time.monotonic() - started_mono,
        detail=redact_message(detail),
        pagination_complete=pagination_complete,
        errors=errors,
        started_at=started_at,
        finished_at=finished_at,
    )
