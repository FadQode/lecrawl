# LexCrawl — Content-Oriented API Implementation Report

- **Date:** 2026-11-05
- **Scope:** Phases 1–3 (API foundation, read repositories + schemas, read endpoints). Crawl triggering and scheduling were designed but **not implemented**, per the task.
- **Uncommitted work preserved:** yes. No commits, no resets, no `.env` changes, no schema/migration changes, no records deleted. Secrets not printed.
- **Database:** configured `lexcrawl` at head `0006`; tests use the isolated `lexcrawl_test`.

---

## 1. Files created and modified

**Created — application (`src/lexcrawl/api/`)**
- `app.py` — `create_app()` factory, lifespan (engine + session factory), router registration under `/api/v1`.
- `dependencies.py` — `get_settings`, `get_session` (request-scoped, rollback-on-error), `get_page_params` and the `SessionDep`/`PageParamsDep` aliases.
- `exceptions.py` — RFC 9457 Problem Details handlers (`NotFoundError`, `DependencyUnavailableError`, `RequestValidationError`, `HTTPException`, unhandled `Exception`).
- `schemas/{common,articles,reports,resources,sources,runs}.py` — pydantic response models (no ORM/ingestion models serialized directly).
- `services/{content_queries,source_queries,resource_queries,run_queries}.py` — SQLAlchemy read queries + classification.
- `routers/{system,sources,articles,reports,resources,runs}.py`.
- `__init__.py` — exports `create_app`, `API_PREFIX`.

**Modified**
- `src/lexcrawl/__init__.py` — added `__version__ = "0.1.0"` for `/health`.

**Created — tests**
- `tests/api/conftest.py`, `tests/api/test_{system,sources,articles,reports,resources,runs,errors}.py`.
- `tests/unit/test_api_classification.py`.

**Untouched (pre-existing empty scaffolding):** `src/lexcrawl/api/routes/__init__.py` (0 bytes) and `src/lexcrawl/scheduler/__init__.py` (0 bytes) were left as-is.

**No changes** to `pyproject.toml`, `config.py`, migrations, `persistence.py`, actors, or repositories — the API reuses the existing services (`persist_crawl_report`, engines, repositories) unchanged.

---

## 2. Implemented endpoints (base `/api/v1`)

| Method | Path | Response | Notes |
| --- | --- | --- | --- |
| GET | `/health` | `{status, version}` | liveness |
| GET | `/ready` | `{status, database}` | 503 Problem Details if PostgreSQL unreachable |
| GET | `/sources` | `[SourceOut]` | orders by code |
| GET | `/sources/{source_code}` | `SourceOut` | 404 if unknown |
| GET | `/articles` | `Page[ArticleSummary]` | `source`, `published_from`, `published_to`, `limit`, `offset` |
| GET | `/articles/{article_id}` | `ArticleDetail` | full content + localizations + resources |
| GET | `/articles/{article_id}/resources` | `[ResourceOut]` | nested |
| GET | `/reports` | `Page[ReportSummary]` | same filters |
| GET | `/reports/{report_id}` | `ReportDetail` | same shape |
| GET | `/reports/{report_id}/resources` | `[ResourceOut]` | nested |
| GET | `/resources` | `Page[ResourceOut]` | `document_id`, `resource_type`, `extraction_status`, `limit`, `offset` |
| GET | `/resources/{resource_id}` | `ResourceOut` | includes parent reference |
| GET | `/crawl-runs` | `Page[RunOut]` | `source`, `status`, `limit`, `offset` |
| GET | `/crawl-runs/{run_id}` | `RunOut` | by UUID |
| GET | `/crawl-runs/{run_id}/errors` | `Page[CrawlErrorOut]` | `stage`, `error_type`, `limit`, `offset` |

`/documents` is deliberately **not** exposed. Pagination is database-level (`LIMIT/OFFSET` with a `COUNT`), ordered deterministically: content by `published_at DESC NULLS LAST, id DESC`; resources by `updated_at DESC, id DESC`; runs by `started_at DESC, id DESC`; errors by `occurred_at DESC, id DESC`.

---

## 3. How article/report classification works

**Verified field:** `documents.document_type` (VARCHAR(64)) is the only classifier; it is not a controlled vocabulary (CBA → `"News Releases"`; Commerce → `"report"`; UAE default → `"news_article"`). The reliable discriminator is the **source**, so the approved approach is a **hybrid** (`services/content_queries.py`):

1. Normalize `document_type` to lowercase.
2. If it is a known article label (`news_article`, `news_release`, `news releases`, `press release`, `article`) → **article**.
3. If it is a known report label (`report`, `reports`, `publication`, `government report`, …) → **report**.
4. Otherwise fall back to the source: `uae_legislation_news`/`cba_news_releases` → article; `commerce_reports` → report; unknown source → unclassified.

The same rules are expressed as a SQL predicate (`_kind_predicate`) for list filtering, so the list and detail paths agree. `/articles/{id}` and `/reports/{id}` fetch by numeric `documents.id` and **404 when the item's kind does not match** (a report id under `/articles` is 404). No content records are duplicated; the same `documents` row is served by both routes based on classification.

---

## 4. Schema changes

**None.** Classification uses the existing `documents.document_type` and `sources.code`; no migration, column, or table was added. This matches the approved decision (hybrid, no `content_kind` column).

---

## 5. Tests added

- `tests/unit/test_api_classification.py` — 11 parametrized classification cases (type wins, source fallback, case-insensitivity, unknown → `None`).
- `tests/api/` — 31 tests across system, sources, articles, reports, resources, runs, errors:
  - app creation, lifespan, DI; health; readiness (200 / 503 when DB unreachable).
  - empty result sets; pagination boundaries (`limit`/`offset`, total across offsets); source filter; publication-date filter.
  - article/report separation and cross-route 404s; detail serialization (`TEXT-BEGIN` content, localizations, resources).
  - resource filters (type, status, document) and parent reference; resource 404.
  - crawl-run listing, detail, error listing with classified stage/type, run filters, 404/422.
  - Problem Details shape for 404/422/500 and secret-leak assertion on the 500 body.

Tests use the isolated `lexcrawl_test` database via fixtures shared from `tests/integration/db/conftest.py`; the readiness-503 and 500 tests use an app without a reachable DB and need no database.

---

## 6. Full test results

| Check | Command | Result |
| --- | --- | --- |
| Full suite | `uv run pytest` | **533 passed** (was 491; +31 API, +11 classification) |
| API tests | `uv run pytest tests/api` | **31 passed** |
| DB integration | `uv run pytest tests/integration/db` | **48 passed** |
| Ruff lint | `uv run ruff check .` | **All checks passed** |
| Ruff format | `uv run ruff format --check .` | **131 files already formatted** |
| Mypy | `uv run mypy src` | **Success: no issues in 74 source files** |

One warning: Starlette deprecation notice about `httpx` in `TestClient` (upstream; not a failure).

---

## 7. Known limitations

- **Authentication:** deferred per instruction ("keep auth for later"). Read endpoints are unauthenticated. The API is nonetheless secure by default: no secrets/config are exposed, error bodies never include stack traces or DSNs, and the 500 handler logs server-side only. `manual_trigger_token` remains unused.
- **No crawl triggering or scheduling** (by design). No job boundary or concurrency guard exists yet.
- **`sources.base_url` is `null`** for all rows (exposed as-is per the approved choice); the payload does not derive the site URL from configuration.
- **No full-text search** (`q` deferred; no suitable index).
- **No resource download** endpoint; resources expose metadata + original URL + extraction status only (as required).
- **Offset pagination** (not cursor); fine for current volumes, noted for later.
- **`published_at` filters** use the date column with a `published_year` fallback for month/year-precision documents; a mid-year `published_from` on a year-precision row is approximate by construction.
- Leftover empty `api/routes/` package remains (inert).

---

## 8. Recommended next implementation phase

1. **Crawl trigger (`POST /api/v1/crawl-runs`) with auth and a concurrency guard.** Required lifecycle design (addresses the `persist_crawl_report` constraint that it currently creates the run at the end):
   1. On request, **create the `crawl_runs` row immediately** with `status="running"` (reuse `repositories/crawl_runs.create_run`), returning `202` with `run_id`.
   2. Admit one run per source (or globally) via a **PostgreSQL advisory lock** or an active-run check → `409` on conflict.
   3. Execute the existing synchronous crawler **off the event loop** (worker thread/process; `run_crawl` + `persist_source_result` against the **pre-created** `crawl_run_id`, so no duplicate run row).
   4. Finalize with `repositories/crawl_runs.finalize_run` (status, `finished_at`, `duration_ms`).
   5. Expose progress through the existing `/crawl-runs/{run_id}` and `/crawl-runs/{run_id}/errors` endpoints.
   Preserve CLI behavior when no run id is supplied (current default).
2. **Authentication** for trigger endpoints: implement a bearer/API-key dependency (constant-time compare against `manual_trigger_token` or a new hashed key), applied only to write routes; keep read endpoints open.
3. **Optional:** cursor pagination and a proper full-text index (`tsvector`/`pg_trgm`) with a migration if `q` search is required.
