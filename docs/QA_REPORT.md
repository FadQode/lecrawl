# LexCrawl API — QA Report

- **Date:** 2026-11-05
- **Scope:** read-only content API (`/api/v1`), Phases 1–3 implementation
- **Result:** **PASS** — no functional defects found; one documentation-quality defect fixed.

## Test environment

- Python 3.12, `uv`; FastAPI + `starlette.testclient` (httpx).
- PostgreSQL 16 in Docker (`lexcrawl-postgres`), configured DB at head `0006`, plus the isolated
  `lexcrawl_test` database for destructive tests.
- API tests migrate `lexcrawl_test` to head and truncate/reseed it before each test; the configured
  database is never modified by tests.
- Live verification used the configured database (real data: 5 CBA articles, 1 Commerce report,
  1 blocked PDF resource, 7 crawl runs).

## Endpoint inventory and coverage

| Method | Path | Purpose | Auth | Test coverage |
| --- | --- | --- | --- | --- |
| GET | `/api/v1/health` | Liveness | – | `test_system::test_health_returns_ok` |
| GET | `/api/v1/ready` | DB readiness (503 on failure) | – | `test_system` (200/503) |
| GET | `/api/v1/sources` | List sources | – | `test_sources::test_list_sources_returns_the_registry` |
| GET | `/api/v1/sources/{source_code}` | One source (404) | – | `test_sources` (found/unknown) |
| GET | `/api/v1/articles` | List/filter articles | – | `test_articles`, `test_qa_pagination`, `test_qa_filtering` |
| GET | `/api/v1/articles/{article_id}` | Article detail | – | `test_articles::test_article_detail_serializes_content_and_localizations` |
| GET | `/api/v1/articles/{article_id}/resources` | Article resources | – | `test_articles::test_article_resources_endpoint` |
| GET | `/api/v1/reports` | List/filter reports | – | `test_reports`, `test_qa_filtering` |
| GET | `/api/v1/reports/{report_id}` | Report detail | – | `test_reports::test_report_detail_and_resources` |
| GET | `/api/v1/reports/{report_id}/resources` | Report resources | – | `test_reports::test_report_detail_and_resources` |
| GET | `/api/v1/resources` | List/filter resources | – | `test_resources`, filters |
| GET | `/api/v1/resources/{resource_id}` | Resource detail (404) | – | `test_resources::test_get_resource_and_not_found` |
| GET | `/api/v1/crawl-runs` | Run history (filters) | – | `test_runs`, `test_qa_queries` |
| GET | `/api/v1/crawl-runs/{run_id}` | Run detail (404/422) | – | `test_runs::test_get_run_and_errors` |
| GET | `/api/v1/crawl-runs/{run_id}/errors` | Run errors (filters) | – | `test_runs::test_get_run_and_errors` |
| POST | `/api/v1/crawl-runs` | Trigger a crawl (202) | Bearer | `test_crawl_trigger` (11 tests) |
| – | `/docs`, `/redoc`, `/openapi.json` | Documentation | – | `test_qa_openapi` |

**Not implemented (documented):** scheduler, full-text search, resource download, `bootstrap`
over the API.

## Scenario coverage

| Area | Scenarios verified |
| --- | --- |
| System | health body/version; readiness 200; readiness 503 when DB unreachable |
| Sources | seeded registry (3); metadata fields; unknown code 404; no secrets in body |
| Articles | article-only listing; source filter; date filter + year fallback; summary excludes body; detail `TEXT-BEGIN` content; localizations; resources; report id under `/articles` → 404 |
| Reports | report-only listing; detail content; resources; article id under `/reports` → 404 |
| Resources | metadata; original URL preserved; blocked status honest (`has_content=false`); parent kind; filters (type/status/document); 404 |
| Crawl runs | list/detail/by-source/status; per-source counters; classified errors; 404/422 |
| Pagination | default limit/offset; minimum; above-max clamp (200); negative → 422; offset beyond; empty set; one page; multiple disjoint pages; NULL dates sort last |
| Filtering | unknown source → empty; combined filters; from>to → empty (accepted); invalid date → 422; classification unchanged |
| Errors | 404/422 Problem Details media type; 500 generic without leaks (`secret`, `Traceback`, DSN absent) |
| OpenAPI | valid 3.1 doc; 15 operations; unique operation ids; summaries/tags; documented 404/422/503; Swagger UI + ReDoc render |
| Query efficiency | article list ≤ 4 SELECTs; run list ≤ 6 SELECTs (no N+1); eager loading verified |
| Crawl trigger | 401 (missing/invalid token); 503 (unconfigured); 422 (bad source/max_pages); 202 with `{id,status,trigger_type:"api"}`; single run row; stable run id through completion; success/partial/failed finalization; same-run persistence; no duplicate rows; 409 on duplicate active source and on `all` vs individual; lock release after worker failure; worker exception → `failed` + `worker_error` |
| Live (real data) | health/ready; `/docs`,`/redoc`,`/openapi.json`; sources; 5 CBA articles; 1 Commerce report; blocked PDF resource; run history; article/report/resource detail; 404/422 problem bodies; controlled `POST /crawl-runs {"source":"cba","max_pages":1}` → 202 then `partial_failure` (5 docs, 5/5 details), read API unchanged |

## Bugs discovered and fixed

1. **`###` docstring markers leaked into public OpenAPI descriptions** (documentation defect).
   - Evidence: `/openapi.json` model descriptions read `"### Public summary of ..."`.
   - Fix: added `clean_docstring_markers()` and a custom `openapi()` that strips the marker from
     public descriptions while keeping the required `###` docstring convention in code.
   - Verified: no `###` in operation or schema descriptions; `/openapi.json` still valid.

2. **`trigger_type` did not allow API runs** (implementation gap surfaced by the new endpoint).
   Added migration `0007_api_trigger_type` widening `ck_crawl_runs_trigger_type_values` to include
   `'api'` and updated the ORM `TriggerType` literal; verified with `alembic check`.

No read-endpoint functional bugs were found. No security issues were found: error bodies never
include stack traces, connection strings, tokens, filesystem paths, or environment values.

## Regression results

The API work did not change the crawler or persistence. Full suite (including all crawler tests):

```
uv run pytest                      -> 564 passed
uv run pytest tests/unit           -> 449 passed
uv run pytest tests/api            -> 62 passed
uv run pytest tests/integration/db -> 48 passed
uv run ruff check .                -> All checks passed
uv run ruff format --check .       -> 141 files already formatted
uv run mypy src                    -> Success: no issues in 76 source files
```

Run counts and skip behavior: with PostgreSQL available, 0 skipped. When PostgreSQL is
unreachable, `tests/api` and `tests/integration/db` **skip** (do not silently pass).

## Known limitations

- **In-process, non-durable crawl execution**: triggered crawls run in an API-process background
  thread, do not survive restarts, and are intended for a single worker. No external queue.
- No scheduler; no full-text search; no resource download; offset pagination; `sources.base_url`
  null; `bootstrap` is CLI-only.
- `published_from`/`published_to` are approximate for year-precision items.
- `422` for "limit above maximum" is not used: the limit is clamped to the configured maximum.
- Upstream `StarletteDeprecationWarning` about `httpx` in `TestClient` (not a defect).

## Remaining risks

- **Content classification is source/type-based**: a future source that publishes two content
  kinds under one source code could be misclassified until the type map is extended.
- **Averages not yet paginated by cursor**: offset pagination can skip/duplicate under concurrent
  writes; acceptable for the current read-only dataset.
- **No auth yet on read paths**: read endpoints are unauthenticated by design; the trigger
  endpoint is protected by a bearer token and an advisory-lock concurrency guard.
