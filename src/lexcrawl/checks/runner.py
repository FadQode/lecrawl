"""### The daily run: probe each source, then crawl only the ones that changed.

The runner builds one acquisition strategy per source and shares it (memoized) between
the change check and the actor, so the listing page is fetched once per run. It also
feeds the actor the set of already-ingested identities, so only new items are fetched.
Two guards apply: an anomaly guard (pause on an unexpected burst of new items) and a
monthly provider-credit budget governor.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from lexcrawl.alerts import ANOMALY, BUDGET_EXCEEDED, CHECK_FAILED, notify
from lexcrawl.checks.base import CheckOutcome, evaluate
from lexcrawl.checks.identities import IdentityStore
from lexcrawl.checks.store import WatermarkStore
from lexcrawl.checks.usage import UsageStore, current_period
from lexcrawl.config import Settings
from lexcrawl.crawling.memo import MemoizingScraper
from lexcrawl.factory import (
    CHECK_SOURCES,
    CREDIT_SOURCES,
    build_actor,
    build_check,
    build_http_fetcher,
    build_scraper,
)
from lexcrawl.logging import get_logger

LOGGER = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class DailySourceResult:
    """### Outcome and accounting for one source in a daily run."""

    source: str
    outcome: CheckOutcome
    documents: int
    skipped_known: int
    pending: int
    credits: int
    duration_seconds: float
    detail: str


def run_daily(
    settings: Settings,
    *,
    sources: tuple[str, ...] = CHECK_SOURCES,
    force: bool = False,
    record: bool = True,
) -> list[DailySourceResult]:
    """### Probe each source, then crawl only the sources that changed."""
    watermark = WatermarkStore(Path(settings.check_state_path))
    identities = IdentityStore(Path(settings.identities_state_path))
    usage = UsageStore(Path(settings.usage_state_path))
    period = current_period()
    return [
        _run_source(name, settings, watermark, identities, usage, period, force=force, record=record)
        for name in sources
    ]


def _run_source(
    name: str,
    settings: Settings,
    watermark: WatermarkStore,
    identities: IdentityStore,
    usage: UsageStore,
    period: str,
    *,
    force: bool,
    record: bool,
) -> DailySourceResult:
    """### Run one source end to end: probe, guard, and crawl when needed."""
    started_at = time.monotonic()
    scraper = build_scraper(name, settings)
    memo = MemoizingScraper(scraper) if scraper is not None else None
    http_fetcher = build_http_fetcher(settings)
    try:
        check = build_check(name, settings, scraper=memo, http_fetcher=http_fetcher)
        key = check.name
        probe = check.probe()
        if record and probe.credits_used:
            usage.add(period, probe.credits_used)
        if not probe.ok:
            notify(CHECK_FAILED, "change check failed", source=key, reason=probe.error)

        verdict: CheckOutcome = "new_data" if force else evaluate(probe, watermark.get(key).fingerprint)
        if verdict != "new_data":
            if record and verdict == "no_change":
                watermark.record(key, probe.fingerprint)
            LOGGER.info("daily_source_finished", extra={"source": key, "outcome": verdict})
            return _result(key, verdict, started_at, credits=probe.credits_used, detail=probe.summary)

        if _budget_exceeded(name, settings, usage, period, force):
            spent = usage.spent(period)
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
                started_at,
                credits=probe.credits_used,
                detail=f"{probe.summary}; spent={spent}/{settings.firecrawl_monthly_credit_cap}",
            )

        known = identities.known(key)
        max_new = _anomaly_limit(settings, known, force)
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
            usage.add(period, run.provider_credits_used)
        credits = probe.credits_used + run.provider_credits_used

        if run.anomaly_detected:
            notify(ANOMALY, "unexpected burst of new items; crawl skipped", source=key)
            LOGGER.warning("daily_source_anomaly", extra={"source": key, "skipped_known": run.records_skipped_known})
            return _result(
                key,
                "anomaly",
                started_at,
                skipped_known=run.records_skipped_known,
                credits=credits,
                detail=f"{probe.summary}; run_status={run.status}",
            )

        ingested = [
            document.identity_key
            for document in run.documents
            if document.identity_key is not None and document.extraction_status == "complete"
        ]
        if record:
            identities.add(key, ingested)
            # $ Advance the watermark only when the run fully succeeded with nothing pending.$
            if run.status == "success" and run.documents_pending == 0:
                watermark.record(key, probe.fingerprint)
        LOGGER.info(
            "daily_source_finished",
            extra={
                "source": key,
                "outcome": verdict,
                "documents": len(run.documents),
                "skipped_known": run.records_skipped_known,
                "credits": credits,
                "run_status": run.status,
            },
        )
        return _result(
            key,
            verdict,
            started_at,
            documents=len(run.documents),
            skipped_known=run.records_skipped_known,
            pending=run.documents_pending,
            credits=credits,
            detail=f"{probe.summary}; run_status={run.status}",
        )
    finally:
        if memo is not None:
            memo.close()
        http_fetcher.close()


def _budget_exceeded(name: str, settings: Settings, usage: UsageStore, period: str, force: bool) -> bool:
    """### Report whether a credit-bearing source has spent its monthly budget."""
    cap = settings.firecrawl_monthly_credit_cap
    return not force and name in CREDIT_SOURCES and cap > 0 and usage.spent(period) >= cap


def _anomaly_limit(settings: Settings, known: frozenset[str], force: bool) -> int | None:
    """### Apply the anomaly limit only on incremental runs with an existing baseline."""
    if force or not known or settings.anomaly_max_new_items <= 0:
        return None
    return settings.anomaly_max_new_items


def _result(
    name: str,
    outcome: CheckOutcome,
    started_at: float,
    *,
    documents: int = 0,
    skipped_known: int = 0,
    pending: int = 0,
    credits: int = 0,
    detail: str = "",
) -> DailySourceResult:
    """### Assemble a per-source daily result."""
    return DailySourceResult(
        source=name,
        outcome=outcome,
        documents=documents,
        skipped_known=skipped_known,
        pending=pending,
        credits=credits,
        duration_seconds=time.monotonic() - started_at,
        detail=detail,
    )
