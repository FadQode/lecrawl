# LexCrawl

LexCrawl collects public legal, regulatory and government publications, turns them into one
canonical document format, and stores them in PostgreSQL behind an authenticated HTTP API.

It crawls **only what changed**: a cheap probe per source decides whether a crawl is needed, already
ingested documents are skipped, and every run is recorded with metrics, credits and errors. Paid
scraping (Firecrawl) is capped by a monthly budget, and one failing source never stops the others.

A companion news-style dashboard, [Lexcrawl-Dashboard](https://github.com/FadQode/lexcrawl-dashboard),
reads from this API.

## Contents

- [Sources](#sources)
- [Architecture](#architecture)
- [How a crawl works](#how-a-crawl-works)
- [Data model](#data-model)
- [Reliability and safety](#reliability-and-safety)
- [Quick start](#quick-start)
- [CLI](#cli)
- [HTTP API](#http-api)
- [Configuration](#configuration)
- [Deployment](#deployment)
- [Project layout](#project-layout)
- [Development](#development)
- [Adding a source](#adding-a-source)
- [Conventions](#conventions)

## Sources

| Source | Code | What is collected | How it is fetched | Change probe | Cost |
| --- | --- | --- | --- | --- | --- |
| Canadian Bankers Association | `cba_news_releases` | News releases (English) | Plain HTTP: the site's JSON listing API, then article pages | Sitemap: article count + newest `lastmod` | Free |
| U.S. Department of Commerce | `commerce_reports` | Reports, with PDF attachments | Firecrawl only (plain HTTP gets a Cloudflare challenge) | Listing page 0: newest report + total count | 1 credit per page |
| UAE Legislation | `uae_legislation_news` | News (Arabic, right-to-left) | Direct HTTP; falls back to Firecrawl only if challenged | Listing: the set of article URLs | Free unless it falls back |

Each source is one entry in the source registry (`src/lexcrawl/sources/registry.py`).

## Architecture

```mermaid
flowchart LR
    subgraph Triggers
        CRON[cron-job.org / any HTTP client] -->|POST /jobs| API
        SCHED[lexcrawl schedule<br/>APScheduler] --> RUNNER
        CLI[lexcrawl daily / crawl] --> RUNNER
    end

    subgraph LexCrawl
        API[FastAPI API] -->|queue| JOBS[(jobs)]
        JOBS -->|claim, SKIP LOCKED| WORKER[Worker<br/>separate process or EMBEDDED_WORKER]
        WORKER --> RUNNER[run_daily]
        RUNNER --> CHECKS[Change probes]
        RUNNER --> ACTORS[Source actors]
        ACTORS --> FETCH[Fetch layer<br/>HTTP / direct / Firecrawl]
        ACTORS --> PROC[Content processing]
        PROC --> RESW[PDF extraction<br/>inline or resource worker]
    end

    FETCH --> WEB[(Source websites)]
    FETCH --> FC[(Firecrawl)]
    CHECKS & PROC & RUNNER & API --> PG[(PostgreSQL)]
    DASH[Lexcrawl-Dashboard] -->|GET, bearer token| API
```

**Layers** (dependencies point inward; the inner layers know nothing about the outer ones):

| Layer | Package | Role |
| --- | --- | --- |
| Models | `models/` | `RawArticle`, `CanonicalDocument`, `DocumentResource`, text marker and hashing |
| Fetching | `crawling/` | One `Fetcher`/`Scraper` contract with HTTP, direct-HTML, Firecrawl, fallback, memoizing and budgeted strategies |
| Actors | `actors/` | Per-source discovery and parsing; `DetailAcquisitionActor` is the shared listing→detail loop |
| Checks | `checks/` | Change probes, checkpoints, identity memory, credit budget, and the `run_daily` orchestrator |
| Persistence | `db/`, `repositories/`, `services/` | SQLAlchemy models, idempotent writes, bounded reads, the job queue, content processing |
| Delivery | `api/`, `cli.py`, `worker.py`, `resource_worker.py`, `scheduler.py` | HTTP API, command line, background workers, cron |
| Composition | `sources/registry.py`, `factory.py` | Builds everything from configuration; the only place that wires layers together |

## How a crawl works

### 1. The daily run (`run_daily`)

Every crawl, whether started by the scheduler, the CLI, the worker or the API, goes through
`checks/runner.run_daily`:

```mermaid
sequenceDiagram
    participant T as Trigger
    participant R as run_daily
    participant L as Advisory locks
    participant P as Change probe
    participant A as Actor
    participant DB as PostgreSQL

    T->>R: run (sources, trigger, run_id)
    R->>L: take one lock per source
    R->>DB: start crawl_runs row
    loop each source (isolated: one failure never stops the rest)
        R->>P: probe (one cheap request)
        P-->>R: fingerprint
        R->>DB: compare with the processed checkpoint
        alt no change, nothing due, no reconciliation due
            R->>DB: record "no_change"
        else new data, or documents due for retry, or reconciliation due
            R->>A: crawl, skipping already-ingested identities
            A-->>R: canonical documents + counters + credits used
            R->>DB: upsert documents and resources, record credits
            R->>DB: advance the processed checkpoint (only if fully ingested)
        end
        R->>DB: crawl_run_sources row + sanitized errors
    end
    R->>DB: finish the crawl_runs row (status, credits, duration)
    R->>L: release locks
```

Key decisions in that loop:

- **Change detection.** Each source keeps two fingerprints in `source_checkpoints`: *observed* (what
  the last probe saw) and *processed* (what has actually been ingested). Decisions compare against
  **processed**, so a probe can never hide a failed ingest.
- **Outcomes.** `no_change` skips the crawl; `new_data` runs it; `check_failed` is treated as a
  failure, never as "no change"; `anomaly` and `budget_exceeded` skip the crawl and raise an alert.
- **Only new items.** The actor asks which candidate identities are already stored
  (`filter_known`) and fetches only the rest.
- **Retries and reconciliation.** Documents whose extraction failed are retried on later runs (up to
  `DOCUMENT_EXTRACTION_ATTEMPTS`), and a full reconciliation crawl runs every
  `RECONCILE_INTERVAL_DAYS`, because a head-and-count fingerprint cannot catch every mid-list edit.
- **Checkpoint safety.** The processed checkpoint advances only when discovery was complete, nothing
  is pending, and every document has text, so anything missed is picked up next time.
- **Bypass.** `--force` and `--bootstrap` crawl regardless of the probe.

### 2. Acquisition per source

```mermaid
flowchart TB
    subgraph UAE
        U1[Direct HTTP<br/>browser navigation headers] -->|200, no challenge| U2[Parse listing and articles]
        U1 -->|403 or challenge| U3[Budgeted Firecrawl] --> U2
    end
    subgraph Commerce
        C1[Budgeted Firecrawl] --> C2[Parse listing and report pages] --> C3[PDF links stored as resources]
    end
    subgraph CBA
        B1[Plain HTTP: JSON listing API] --> B2[Article pages] --> B3[Parse]
    end
```

- **Run-scoped memoization.** The page a probe fetched is reused by the actor in the same run, so it
  is never paid for twice.
- **Firecrawl response cache.** Successful detail pages are cached on disk (`FIRECRAWL_CACHE_DIR`,
  7-day TTL), but **only after they parse correctly**; a cached page that later fails to parse is
  evicted, so a bad response can never be replayed forever.
- **Budget.** `BudgetedScraper` checks the monthly credit total before **every** Firecrawl call. When
  `FIRECRAWL_MONTHLY_CREDIT_CAP` is reached the run stops spending, the remaining items are marked
  pending for a later run, and no failed attempt is recorded against them. UAE keeps crawling on its
  free direct path.
- **Limits per run.** `CRAWL_MAX_LISTING_PAGES`, `FIRECRAWL_MAX_PAGES_PER_RUN`,
  `FIRECRAWL_MAX_DETAIL_SCRAPES_PER_RUN` and `UAE_MAX_DETAIL_SCRAPES_PER_RUN` bound each crawl.
- **Anomaly guard.** If an incremental run finds more than `ANOMALY_MAX_NEW_ITEMS` new items, the
  crawl is skipped and an alert is raised (`--force` and the first run bypass this).

### 3. Documents and attachments

`services/content_processing.ContentProcessingService` persists a batch of canonical documents and
drives attachment (PDF) extraction:

1. **Stage** (one short transaction): upsert documents and resources, then durably **claim** due
   PDFs by setting `claimed_at` (a lease of `RESOURCE_CLAIM_LEASE_SECONDS`).
2. **Fetch** (no transaction held): download each PDF, streaming and aborting past
   `RESOURCE_MAX_BYTES`, only from hosts in `RESOURCE_ALLOWED_HOST_SUFFIXES`.
3. **Extract**: PyMuPDF text extraction.
4. **Record** (one short transaction): mark each attachment `complete` or `failed`, clear the claim.

Attachments are retried up to `RESOURCE_EXTRACTION_ATTEMPTS`, then left `failed`. A sweep on every
run also retries due attachments of documents that were not crawled again. For heavier loads, the
resource worker (`lexcrawl worker --queue resources`) runs extraction in a process pool, separately
from crawling.

### 4. Jobs through the API

```mermaid
sequenceDiagram
    participant C as Client (e.g. cron-job.org)
    participant API as FastAPI
    participant DB as PostgreSQL
    participant W as Worker (thread or process)

    C->>API: POST /jobs {"trigger":"api"} + bearer token
    API->>DB: insert one queued job per source
    Note over DB: partial unique index: one active job per source
    API-->>C: 202 Accepted (run_id per source) or 409 if active
    API->>W: kick the embedded worker (when EMBEDDED_WORKER=true)
    W->>DB: claim the oldest queued job (SKIP LOCKED)
    W->>W: run_daily for that source, heartbeat while running
    W->>DB: mark succeeded/failed + result_status
    C->>API: GET /runs/{run_id}
```

The worker reclaims jobs whose claim lease expired (`JOB_CLAIM_LEASE_SECONDS`) and marks orphaned
`running` runs as `abandoned`, but only when no source lock is held, so a live crawl is never
touched. With `EMBEDDED_WORKER=true` the API runs queued jobs on a background thread after each
`POST /jobs` and once at startup, which suits single-container hosts; otherwise run
`lexcrawl worker` as its own process.

## Data model

| Table | Holds |
| --- | --- |
| `sources` | One row per source; seeded from the registry at startup (idempotent) |
| `documents` | Canonical documents, one per `(source_id, identity_key)`; monotonic upserts mean a failed re-fetch never replaces good text |
| `document_resources` | Attachments with their own status, attempts and claim lease |
| `source_checkpoints` | Observed and processed fingerprints per source |
| `crawl_runs` | One row per execution: trigger, status, duration, credits |
| `crawl_run_sources` | Per-source metrics per run: outcome, discovered/inserted/updated/skipped/failed, pending, credits |
| `crawl_errors` | Sanitized errors (stage, type, redacted message) |
| `jobs` | The job queue: status, claim, heartbeat, `result_status`, per-source uniqueness |

Views: `v_pending_documents` (retryable documents) and `v_monthly_credit_usage` (credits per source
and month, derived from run records). Migrations are in `migrations/versions/` and are append-only.

## Reliability and safety

- **Isolation.** Each source runs inside its own error boundary; one crashing source is recorded as
  failed while the others continue.
- **Concurrency.** A PostgreSQL advisory lock per source is held for the whole run and released only
  after the run record is finalized, so two processes cannot crawl the same source, a live run is
  never mistaken for an abandoned one, and different sources can run in parallel. The scheduler
  holds its own lock, so only one scheduler runs.
- **Idempotency.** Document and resource writes are upserts keyed by identity; `POST /jobs` accepts
  an `Idempotency-Key`.
- **Network safety.** Fetches only go to configured source hosts (attachments to the allowed
  suffixes), redirects are re-validated against the allowlist, non-public IP literals are refused,
  and response bodies are size-capped while streaming.
- **Polite crawling.** Identifying user agent, per-host request pacing, bounded retries with
  `Retry-After` support, and no attempts to solve or evade bot challenges.
- **Secrets.** Credentials in URLs and messages are redacted before logging or storage; the API
  compares tokens in constant time.
- **Observability.** Structured JSON logs carry `run_id`; every run and error is in the database; the
  run record is finalized even if execution throws; `/metrics` exposes Prometheus gauges.

## Quick start

Requires Python 3.12, [uv](https://docs.astral.sh/uv/) and PostgreSQL (or Docker).

```bash
uv sync
cp .env.example .env               # set DATABASE_URL, API_TOKEN, FIRECRAWL_API_KEY, source URLs
uv run alembic upgrade head        # create the schema
uv run lexcrawl daily --source cba # first crawl (CBA needs no Firecrawl credits)
uv run lexcrawl serve              # API on :8000, Swagger UI at /docs
```

Or run the whole stack with Docker:

```bash
POSTGRES_PASSWORD=change-me API_TOKEN=change-me docker compose up --build
```

That starts `db`, `migrate`, `api`, `scheduler`, `worker` and `resource-worker`.

## CLI

| Command | What it does |
| --- | --- |
| `lexcrawl daily [--source S\|all] [--force] [--bootstrap] [--dry-run]` | Probe each source and crawl only what changed (the normal entry point) |
| `lexcrawl check [--source S\|all] [--dry-run]` | Run the change probes only; exit 0 = no change, 1 = new data, 2 = check failed |
| `lexcrawl crawl --source S [--max-pages N] [--out PATH]` | Run one actor and write canonical JSON to a file, without the database |
| `lexcrawl schedule` | Run `daily` on `SCHEDULE_DAILY_CRON` until stopped |
| `lexcrawl serve [--host H] [--port P]` | Start the HTTP API |
| `lexcrawl worker [--once] [--queue crawl\|resources]` | Process queued crawl jobs (default), or due PDF attachments |

## HTTP API

Every endpoint except `/healthz`, `/readyz` and `/metrics` requires `Authorization: Bearer <API_TOKEN>`.
If `API_TOKEN` is unset the API answers `503`; a missing or wrong token gets `401`.

| Method and path | Returns |
| --- | --- |
| `GET /healthz` | Process is up |
| `GET /readyz` | Database is reachable |
| `GET /metrics` | Prometheus metrics (runs, durations, credits, failures, queue depth) |
| `GET /sources`, `GET /sources/{code}` | Registered sources |
| `GET /documents` | Document metadata, newest first; filters `source`, `status`, `document_type`, `topic`, `published_from`, `published_to`; cursor paging, `limit` ≤ 200; never includes text |
| `GET /documents/{id}` | One document including its text |
| `GET /documents/{id}/resources` | Its attachments and their status |
| `GET /documents/{id}/text` | Text and content hash only |
| `GET /runs`, `GET /runs/{run_id}` | Run history; one run with per-source metrics and errors (`error_limit` ≤ 500) |
| `GET /analytics/credits` | Credits per source and month |
| `GET /analytics/pending` | Documents still due for another attempt |
| `POST /jobs` | Queue a crawl. Body `{"sources": [...]?, "trigger": "api"\|"manual"}`, optional `Idempotency-Key`. Returns `202` with one job per source, or `409` if a job for a source is already active |

Interactive documentation is served at `/docs`.

## Configuration

All settings are environment variables (or `.env`); `.env.example` lists every one. The most
important:

| Variable | Default | Purpose |
| --- | --- | --- |
| `DATABASE_URL` | — | `postgresql+psycopg://user:pass@host:port/db` (use a direct, non-pooled connection: advisory locks need session pooling). Alternatively `DB_HOST`/`DB_USER`/`DB_PASSWORD`/`DB_NAME` |
| `API_TOKEN` | — | Bearer token for the API |
| `FIRECRAWL_API_KEY` | — | Needed for Commerce, and for UAE when it falls back |
| `LISTINGS_URL`, `RELEASES_API_URL`, `RELEASES_SITE_URL`, `RELEASES_TYPE_ID`, `REPORTS_LISTING_URL` | — | Source entry points (UAE, CBA, Commerce) |
| `CRAWL_USER_AGENT` | `LexCrawl/0.1 (+…)` | Identifying user agent; include a contact URL |
| `FIRECRAWL_MONTHLY_CREDIT_CAP` | `900` | Monthly Firecrawl budget (`0` disables the cap) |
| `ANOMALY_MAX_NEW_ITEMS` | `10` | New items above which an incremental crawl is held for review |
| `EMBEDDED_WORKER` | `false` | Run queued jobs inside the API process |
| `SCHEDULE_ENABLED`, `SCHEDULE_DAILY_CRON`, `SCHEDULE_TIMEZONE` | `true`, `0 6 * * *`, `UTC` | In-app scheduler |
| `RECONCILE_INTERVAL_DAYS` | `7` | Full reconciliation crawl interval (`0` disables) |
| `RESOURCE_ALLOWED_HOST_SUFFIXES` | `[".gov", "cba.ca"]` | Hosts PDFs may be downloaded from |
| `RESOURCE_MAX_BYTES` | `20000000` | Maximum attachment size |

## Deployment

**Docker Compose** (`compose.yaml`) runs everything on one machine: PostgreSQL, migrations, the API,
the scheduler, the crawl worker and the resource worker. Set `POSTGRES_PASSWORD` and `API_TOKEN`;
the image runs as a non-root user and persists the Firecrawl cache in the `crawlstate` volume.

**Free hosted setup** (what the project currently runs on):

```mermaid
flowchart LR
    CJ[cron-job.org<br/>daily POST /jobs] --> SD[SnapDeploy container<br/>API + EMBEDDED_WORKER]
    KA[cron-job.org<br/>GET /healthz every 5 min<br/>around crawl time] --> SD
    SD --> NEON[(Neon PostgreSQL)]
    DASH[Lexcrawl-Dashboard on Vercel] --> SD
```

1. Create a Neon database and run `alembic upgrade head` against it once.
2. Deploy the Docker image (for example on SnapDeploy) with `DATABASE_URL`, `API_TOKEN`,
   `FIRECRAWL_API_KEY`, `EMBEDDED_WORKER=true`, `SCHEDULE_ENABLED=false` and the source URLs.
3. On cron-job.org, schedule `POST /jobs` with `Authorization: Bearer <API_TOKEN>`,
   `Content-Type: application/json` and body `{"trigger":"api"}` once a day. Hosts that sleep when
   idle need a second job calling `GET /healthz` every few minutes for about an hour around the
   crawl, so the container stays awake until the crawl finishes.

`.github/workflows/crawl.yml` can also run a crawl on demand (`workflow_dispatch`) against the
database in the `DATABASE_URL` secret.

## Project layout

```text
src/lexcrawl/
├── sources/registry.py     # SourceSpec per source: scraper, check, actor, redirect hosts
├── factory.py              # composition root: runtime, fetchers, document memory
├── actors/
│   ├── base.py             # Actor contract, ActorRunResult counters, partition_known
│   ├── detail.py           # DetailAcquisitionActor: shared listing → detail loop
│   ├── cba.py              # CBA listing API + article parsing
│   ├── commerce.py         # Commerce listing + report parsing
│   └── uae_legislation.py  # UAE listing + article parsing (Arabic)
├── checks/
│   ├── runner.py           # run_daily: probe, decide, crawl, persist, record
│   ├── cba.py, uae.py, commerce.py   # per-source change probes
│   ├── change_detection.py, fingerprints.py, checkpoints.py
│   ├── identities.py       # identity memory (what is already ingested)
│   ├── budget.py           # monthly credit budget
│   └── run_records.py      # crawl_runs / crawl_run_sources / crawl_errors recorder
├── crawling/
│   ├── base.py             # Fetcher contract, errors, URL validation, redaction
│   ├── http.py             # httpx fetcher: retries, pacing, redirect checks, size caps, LRU cache
│   ├── direct.py           # direct HTML with navigation headers; fails closed on a challenge
│   ├── fallback.py         # direct first, Firecrawl only when challenged
│   ├── firecrawl.py        # Firecrawl v2 client with validated-response cache
│   ├── budgeted.py         # stops paid calls at the monthly cap
│   ├── memo.py             # run-scoped page reuse
│   └── scrape.py           # provider-neutral scraping contract
├── models/document.py      # RawArticle, CanonicalDocument, DocumentResource, hashing
├── pipeline/               # text normalization, PDF extraction
├── services/               # content processing, credit accounting, attachment fetchers
├── repositories/           # documents, resources, jobs, reads, analytics, checkpoints, runs
├── db/                     # SQLAlchemy models, engine, sessions
├── api/
│   ├── app.py              # FastAPI factory (startup seeding, embedded worker)
│   ├── background.py       # QueueDrainer: runs queued jobs on a background thread
│   ├── auth.py, contracts.py, dependencies.py
│   └── routes/             # health, metrics, sources, documents, runs, analytics, jobs
├── worker.py               # job worker (heartbeat, lease reclaim, abandoned-run cleanup)
├── resource_worker.py      # PDF extraction worker (process pool)
├── scheduler.py            # APScheduler daily job
├── locking.py              # PostgreSQL advisory locks
├── metrics.py, alerts.py, logging.py, config.py, cli.py
migrations/versions/        # Alembic migrations (append-only)
tests/unit/, tests/db/, tests/e2e/   # unit tests, PostgreSQL-backed tests, end-to-end pipeline
```

## Development

```bash
uv run alembic upgrade head                          # the db tests need a migrated database
uv run pytest --cov=lexcrawl --cov-fail-under=70
uv run ruff check .
uv run mypy                                          # strict
```

Unit tests mock the network (`respx`); tests marked `db` run against PostgreSQL. CI
(`.github/workflows/ci.yml`) runs ruff, mypy and pytest with coverage on every push and pull
request.

## Adding a source

1. Write an actor in `actors/`. A source that reads a listing and then a page per item can subclass
   `DetailAcquisitionActor` and implement `_discover`, `_parse`, `_identity`, `_detail_url`,
   `_store_document`, `_store_partial` and `_fail_item`.
2. Write a change probe in `checks/`.
3. Add one `SourceSpec` to `REGISTRY` in `sources/registry.py` with its `code`, `display_name`,
   `base_url`, builders and redirect hosts.

The CLI choices, the API's accepted source codes, the HTTP allowlists and the `sources` table all
derive from the registry, so nothing else needs editing.

## Conventions

Required by the original technical assignment: every function docstring begins with `###`, every
code comment ends with `$`, and every collected text document starts with `TEXT-BEGIN`.
