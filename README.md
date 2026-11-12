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
| Daily incremental run (`lexcrawl daily`): probe → crawl only changes | Implemented |
| Daily scheduler (`lexcrawl schedule`, APScheduler) + run lock | Implemented |
| Anomaly guard + monthly credit budget governor | Implemented |
| PDF attachment extraction | Not started (attachment URLs captured as metadata) |
| PostgreSQL persistence | Not started |
| FastAPI + Swagger UI | Not started |
| Docker Compose | Not started |

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
├── factory.py         # composition root: builds checks, actors, and scrapers
├── cli.py             # `lexcrawl crawl` / `check` / `daily` / `schedule`
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
