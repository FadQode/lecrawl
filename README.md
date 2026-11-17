# LexCrawl

Crawling and document-ingestion system for public legal, regulatory, and government
information. The working architecture guide lives in `.agents/AGENTS.MD`.

This repository is being rebuilt **thin, one source at a time**. All three sources — CBA news
releases, Commerce reports, and UAE Legislation news — crawl to canonical JSON; persistence, the
API, and scheduling are planned next.

## Status

| Layer | State |
| --- | --- |
| CBA news releases (HTTP → parse → canonical JSON) | Implemented |
| Commerce reports (Firecrawl → parse → canonical JSON) | Implemented |
| UAE Legislation news (direct fetch → Firecrawl fallback → parse → canonical JSON) | Implemented |
| Change detection for all sources (`lexcrawl check`) | Implemented |
| Change-detection checkpoints (`source_checkpoints`, observed vs processed) | Implemented |
| Crawl monitoring orchestrator (retry-pending + periodic reconciliation) | Implemented |
| Daily incremental run (`lexcrawl daily`): probe → crawl only changes | Implemented |
| Observability: `crawl_runs` + `crawl_run_sources` + `crawl_errors` + views | Implemented |
| Read/query layer (keyset pagination, detail, runs, resources) | Implemented |
| Credit accounting (actual + reserved, DB-authoritative) | Implemented |
| Advisory-lock run coordination + durable job queue + worker | Implemented |
| API response contracts (models only; routes not started) | Implemented |
| Per-source failure isolation (one source can't abort the others) | Implemented |
| Daily scheduler (`lexcrawl schedule`, APScheduler) + run lock | Implemented |
| Anomaly guard + monthly credit budget governor | Implemented |
| PostgreSQL persistence (`sources`, `documents`, `document_resources`) | Implemented |
| Content processing service + PDF extraction pipeline (opt-in) | Implemented |
| PDF attachment extraction wired live per source | Not started (dormant until a byte fetcher is provided) |
| FastAPI API (reads + job submission; Swagger at /docs) | Implemented |
| Docker Compose (db + api + worker) | Implemented |
| GitHub Actions daily crawl (00:00 UTC) | Implemented |

## Layout

```text
src/lexcrawl/
├── actors/
│   ├── base.py        # Actor contract + ActorRunResult counters
│   ├── cba.py         # CBA listing feed + article page parsing and Actor
│   ├── commerce.py    # Commerce reports listing + detail parsing over Firecrawl
│   └── uae_legislation.py  # UAE news listing + article parsing over Firecrawl
├── crawling/
│   ├── base.py        # Fetcher protocol, FetchedPage, fetch errors, block detection
│   ├── http.py        # httpx strategy: timeouts, bounded retries, pacing, redirect checks
│   ├── scrape.py      # provider-neutral scraping contract (ScrapedPage, Scraper, errors)
│   ├── direct.py      # direct HTML acquisition with navigation headers, fail closed on a challenge
│   ├── fallback.py    # tries the direct path, falls back to Firecrawl only on a challenge
│   ├── memo.py        # run-scoped memoization so one run fetches a page once
│   └── firecrawl.py   # Firecrawl v2 scrape client (retries, pacing, response cache)
├── models/document.py # RawArticle, CanonicalDocument, DocumentResource, TEXT-BEGIN, hashing
├── pipeline/normalize.py  # shared whitespace and paragraph extraction
├── orchestrator.py    # runs actors in isolation, aggregates the run report
├── checks/            # cheap per-source change probes that gate the actors
│   ├── base.py        # Check contract, CheckProbe, evaluate() verdict
│   ├── store.py       # JSON watermark store (moved to the database later)
│   ├── identities.py  # JSON identity store: what has already been ingested
│   ├── usage.py       # JSON monthly provider-credit usage (budget governor)
│   ├── runner.py      # run_daily: probe, guard, then crawl only what changed
│   ├── uae.py         # fingerprint the listing's article set (free)
│   ├── cba.py         # fingerprint the sitemap's article count + newest lastmod (free)
│   └── commerce.py    # fingerprint the listing's newest report + total count (Firecrawl)
├── scheduler.py       # APScheduler daily job around run_daily (`lexcrawl schedule`)
├── locking.py         # advisory file lock (scheduled vs manual runs)
├── alerts.py          # structured alert hook (anomaly / budget / check_failed)
├── factory.py         # composition root: builds checks, actors, scrapers, memory
├── cli.py             # `lexcrawl crawl` / `check` / `daily` / `schedule`
├── db/                # SQLAlchemy models, engine, sessions
├── api/               # response contracts (pre-FastAPI)
├── repositories/      # idempotent writes, bounded reads, jobs, accounting
├── services/          # content processing: persistence + attachment extraction
├── pipeline/          # normalization + PDF text extraction
└── migrations/        # Alembic migrations
├── config.py          # validated settings
└── logging.py         # structured JSON logging
tests/unit/            # parser, fetcher, contract, CLI tests (mocked network)
tests/samples.py       # short structural fixtures (no archived pages)
```

Dependency direction: `models` (pure contracts) is innermost; `crawling` is the external I/O
detail; `actors` depend on `models`, `pipeline`, and the `crawling` contracts; `orchestrator`
depends on `actors`; `cli` is the composition root.

## Quick start

```bash
uv sync
cp .env.example .env          # set CRAWL_USER_AGENT and FIRECRAWL_API_KEY
uv run lexcrawl crawl --source cba
uv run lexcrawl crawl --source commerce --max-pages 1
uv run lexcrawl crawl --source uae --max-pages 1
uv run lexcrawl crawl --source cba --max-pages 1 --out data/cba.json

# change detection: gate a crawl on whether the source actually changed
uv run lexcrawl check --source all        # exit 0 = no change, 1 = new data, 2 = check failed
uv run lexcrawl check --source uae --dry-run

# daily incremental run: probe each source, then crawl only the ones that changed
uv run lexcrawl daily                     # exit 0 = no change, 1 = new data, 2 = check failed
uv run lexcrawl daily --source uae
uv run lexcrawl daily --force             # bypass change detection

# daily scheduler: run the daily job on a cron until stopped (Ctrl-C / SIGTERM)
uv run lexcrawl schedule
```

- `--out` takes a file path or a directory. A directory (the default, `OUTPUT_DIR=data`)
  receives `<UTC timestamp>-<source>.json`.
- The output is one JSON array of canonical documents; a document's `text` starts with
  `TEXT-BEGIN` exactly once and `content_hash` is present only when there is text.
- Exit code is `0` only when the run reports `success`; a partial or empty run exits `1`.

## Testing

```bash
uv run pytest
uv run ruff check .
uv run mypy
```

## Daily run

`lexcrawl daily` probes each source cheaply and crawls only the ones that changed:

- **UAE** — one direct listing GET (free); the actor **reuses that same page** (run-scoped
  memoization), so the listing is fetched once, not twice.
- **CBA** — one sitemap GET (free).
- **Commerce** — one Firecrawl listing fetch (1 credit); the actor reuses it.

When a source changed, the actor is handed the set of **already-ingested identities** and fetches
only new items. The watermark advances only after a fully successful run (nothing pending), so a
failed fetch is retried next run. State lives in `data/state/checks.json` (watermarks) and
`data/state/identities.json` (identities) until the PostgreSQL layer lands.

## Persistence

State lives in PostgreSQL by default (`STATE_BACKEND=db`); the JSON stores remain available for
tests and explicit local development (`STATE_BACKEND=json`). Apply the schema with:

```bash
uv run alembic upgrade head
```

Phase 1 stores `sources` (a seeded registry) and `documents` — the canonical projection, one row
per source + identity, written with monotonic upserts (a failed re-fetch never replaces good
content). "What have we already ingested?" is now a query over `documents`; watermarks and monthly
usage still use the JSON stores until their tables land.

Phase 2 adds `document_resources` and a `ContentProcessingService` that persists a document with
its attachments and drives **per-attachment** extraction: each PDF is processed independently, with
its own status, attempts, and `last_extracted_at`, retried up to `RESOURCE_EXTRACTION_ATTEMPTS`
(default 3) and then left in a terminal `failed` state. Normalization/validation/hashing stay in the
canonical models; the service only orchestrates. One SQLAlchemy engine per process is shared by
document memory, the checkpoint store, and the run recorder via `build_runtime`. Network fetching runs **outside** any DB
transaction; claims are **durable** (`claimed_at` + `RESOURCE_CLAIM_LEASE_SECONDS`) so other
workers/hosts skip a row already being fetched and a crashed claim expires; the byte cap
(`RESOURCE_MAX_BYTES`) is enforced by the fetcher **while streaming**; and attachment errors are
redacted before being logged or stored. PDF extraction is **dormant** until a per-source fetcher is
wired in (so we don't spend Firecrawl PDF credits by default).

**Attachment (PDF) policy:** extraction is opt-in. To enable it, construct a per-source
`ResourceFetcher` (`HttpResourceFetcher` streams and aborts past `RESOURCE_MAX_BYTES`) and pass it to
`DbDocumentMemory`. Cost note: Firecrawl parses PDFs at **1 credit per PDF page**, so enable it
selectively and gate by page count; until then attachments are stored as metadata with status
`pending` and retried up to `RESOURCE_EXTRACTION_ATTEMPTS`.

## Observability

Every execution writes exactly one `crawl_runs` row (trigger, status, duration, credits) and one
`crawl_run_sources` row per source (check outcome, status, discovered/inserted/updated/skipped/failed
counts, resource count, pending backlog, credits, pagination, timing). Sanitized errors go to
`crawl_errors` (stage + type + redacted message). Two views support reporting:
`v_pending_documents` (retryable documents) and `v_monthly_credit_usage` (per-source credits derived
from run records — no drifting counter). Structured logs carry `run_id` and `crawl_run_source_id` so
application output correlates with the database. The run record is finalized even if execution
throws. Rows recorded before fine-grained metrics existed are marked `metrics_known = false`
(status `unknown`) by a follow-up migration; migrations are append-only (applied files are not
rewritten).

## Change detection (Phase 3)

`source_checkpoints` stores, per source, the **observed** fingerprint (what the last probe saw)
and the **processed** fingerprint (what has actually been ingested). Decisions compare against
**processed**, so a probe that sees a change cannot mask a failed ingest:

- `no_change` → skip the crawl.
- `new_data` → run the actor; advance **processed** only after the discovery set is persisted and
  nothing is left pending (per-document extraction failures retry via a due-count, not by holding
  the checkpoint back forever).
- `check_failed` → fail safe: never treated as `no_change`, never triggers a crawl.
- `--force` / `--bootstrap` bypass the comparison.
- Pending documents are retried independently of new-data detection, and a **periodic
  reconciliation** (`RECONCILE_INTERVAL_DAYS`) re-crawls after a long gap because a head+count
  fingerprint cannot catch every mid-list edit.

Monitors are `checks/*` (freedom: CBA sitemap, UAE listing, Commerce listing head+count); the
orchestrator is `checks/runner.run_daily`; fingerprints live in `checks/fingerprints.py`.

## Scheduler and guards

`lexcrawl schedule` runs `run_daily` on a daily cron (default `0 6 * * *` UTC, configurable via
`SCHEDULE_DAILY_CRON`, `SCHEDULE_TIMEZONE`, `SCHEDULE_ENABLED`). It holds an advisory lock so two
schedulers — or a manual `lexcrawl daily` — cannot run at once; APScheduler also uses
`max_instances=1`.

Two guards run during `daily`:

- **Anomaly guard** — if an incremental run finds more than `ANOMALY_MAX_NEW_ITEMS` (default 10) new
  items, the crawl is skipped, the outcome is `anomaly`, the watermark is not advanced, and an alert
  is logged. `--force` and the first (bootstrap) run bypass it.
- **Budget governor** — monthly Firecrawl credits are tracked in `usage.json`; once
  `FIRECRAWL_MONTHLY_CREDIT_CAP` (default 900) is reached, a credit-bearing source's (Commerce) crawl
  is skipped — the probe still runs — with a `budget_exceeded` outcome and an alert.

Alerts are structured logs today (`alerts.notify`), with a hook for a webhook/email later.

## API and scheduling

```bash
uv run lexcrawl serve --port 8000      # FastAPI + Swagger at /docs
uv run lexcrawl worker                 # claims queued jobs (or --once)
docker compose up --build              # db + migrate + api + worker
```

**Authentication policy:** every endpoint except the health probes (`/healthz`, `/readyz`) requires
`Authorization: Bearer <API_TOKEN>`. Auth is constant-time; if `API_TOKEN` is unset the API returns
`503`, and missing/invalid tokens return `401`.

Read endpoints (repository-backed, no SQL in handlers): `GET /sources`, `/sources/{code}`,
`/documents` (filters + cursor + `limit<=200`, **never** returns `text`), `/documents/{id}`,
`/documents/{id}/resources`, `/documents/{id}/text`, `/runs`, `/runs/{run_id}` (errors bounded by
`error_limit<=500`, with `errors_total`), `/analytics/credits`, `/analytics/pending`.

`POST /jobs` validates source codes, accepts `Idempotency-Key` (≤128 chars), returns **202** with a
`run_id`, and **409** when a job is already active. Single-active execution is enforced atomically by
a PostgreSQL partial unique index on `jobs` (plus the advisory lock during the crawl). The worker
claims jobs with `SKIP LOCKED`, holds the advisory lock, and executes `run_daily`. GitHub Actions runs
`lexcrawl daily --source all` daily and via `workflow_dispatch`.

## Pre-API hardening

- **Reads** (`repositories/reads.py`): bounded keyset pagination (`published_at DESC NULLS LAST,
  id DESC`), filter-bound cursors, list projections without `text`, and explicit detail/resources/
  sources/runs/errors queries.
- **Indexes**: `(source_id, published_at DESC NULLS LAST, id DESC)`, `(published_at DESC NULLS
  LAST, id DESC)`, `GIN(topics)` — validated with `EXPLAIN` (the list query uses an index-only scan).
- **Credits** (`services/credit_accounting.py`): actual + reserved from PostgreSQL; the governor and
  analytics share it. The JSON `UsageStore` remains only for `STATE_BACKEND=json`.
- **Run coordination** (`locking.AdvisoryLock`): a session-level PostgreSQL advisory lock held on a
  dedicated connection for the whole crawl; the scheduler and worker share the key.
- **Job queue** (`jobs` table + `repositories/jobs.py`): idempotent enqueue, `SKIP LOCKED` claim,
  stale-claim reclaim; `lexcrawl worker` claims and executes jobs (`--once` or loop).
- **Contracts** (`api/contracts.py`): list/detail/run response models, pagination envelope, error
  and job-accepted envelopes — defined ahead of the FastAPI routes.

## Source access

- CBA is fetched over plain HTTP (server-rendered pages and its listing feed).
- Commerce is acquired through Firecrawl only: plain HTTP is refused with a Cloudflare challenge.
- UAE is fetched **directly** with standard navigation headers (`Accept`, `Accept-Language`,
  `Sec-Fetch-*`, `Upgrade-Insecure-Requests`) and the identifying user agent. Following the
  AGENTS.md exception, this is allowed only while the source answers 200 with no challenge. Any
  403 or challenge response **fails closed** to Firecrawl after a single same-request retry;
  variations, fingerprint spoofing, and challenge solving are never attempted. Set
  `UAE_DIRECT_FETCH=false` to use Firecrawl for UAE exclusively.

## Conventions

Per the technical assignment: every function docstring begins with `###`, every code comment
ends with `$`, and every collected text document starts with `TEXT-BEGIN`.
