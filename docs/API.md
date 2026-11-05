# LexCrawl API Reference

- **Version:** 0.1.0
- **Base path:** `/api/v1`
- **Content type:** `application/json`; errors use `application/problem+json`
- **Interactive docs:** `/docs` (Swagger UI), `/redoc` (ReDoc), `/openapi.json` (OpenAPI 3.1)

## Overview

LexCrawl is a multi-source crawling and content-extraction platform. It discovers content from
configured websites, visits detail pages, extracts and normalizes the content, validates it, and
persists it. This API exposes the collected content **read-only**.

The API is **content-oriented**. It does not expose the internal `documents` persistence table or
its terminology.

### Content terminology

| Term | Meaning |
| --- | --- |
| **Article** | A news article or news release (e.g. UAE Legislation news, CBA news releases). |
| **Report** | A report or publication (e.g. U.S. Department of Commerce reports). |
| **Resource** | A linked file or attachment (PDF, Excel, Word, CSV, other) belonging to an article or report. |
| **Source** | A website configured for crawling. |
| **Crawl run** | One crawler execution, with per-source results and errors. |

A **report** and its linked **PDF attachment** are different objects: the report is the content
item (with extracted body text); the PDF is a **resource** attached to it.

## Authentication

Read endpoints are **unauthenticated**. The crawl-trigger endpoint (`POST /api/v1/crawl-runs`)
requires a **bearer token** matching the server's `MANUAL_TRIGGER_TOKEN`:

```http
Authorization: Bearer <MANUAL_TRIGGER_TOKEN>
```

- Missing or invalid token → `401` (Problem Details, with `WWW-Authenticate: Bearer`).
- Token not configured on the server → `503` (triggering disabled).
- The token is never echoed in responses or logs.

The API never returns configuration, credentials, connection strings, or stack traces.

## Running the API

```bash
uv run uvicorn lexcrawl.api:create_app --factory --host 0.0.0.0 --port 8000
```

Then open <http://localhost:8000/docs> (Swagger UI) or <http://localhost:8000/redoc> (ReDoc).

## Conventions

### Pagination envelope

Every list endpoint returns:

```json
{
  "items": [],
  "pagination": { "limit": 25, "offset": 0, "total": 0 }
}
```

- `limit` defaults to `API_PAGE_SIZE_DEFAULT` (25) and is clamped to `API_PAGE_SIZE_MAX` (200).
- `offset` defaults to 0. `limit < 1` or `offset < 0` is a `422`.
- `total` is the full match count, independent of the page.

### Ordering

- Articles and reports: `published_at` **descending, NULLs last**, then `id` descending.
- Resources: `updated_at` descending, then `id` descending.
- Crawl runs: `started_at` descending, then `id` descending.
- Run errors: `occurred_at` descending, then `id` descending.

### Filtering

- `source` filters by source code (an unknown code is a valid filter that matches nothing).
- `published_from` / `published_to` filter articles and reports by publication date, inclusive,
  `YYYY-MM-DD`. Items stored with only a year are matched by a same-year range.
- `resource_type` and `extraction_status` filter resources; `document_id` filters by parent.
- `status` and `source` filter crawl runs; `stage` and `error_type` filter run errors.

### Errors (RFC 9457 Problem Details)

```json
{
  "type": "about:blank",
  "title": "Not Found",
  "status": 404,
  "detail": "article 999999 was not found",
  "instance": "/api/v1/articles/999999"
}
```

| Status | Meaning |
| --- | --- |
| 200 | Successful read. |
| 404 | The requested resource does not exist. |
| 422 | Request parameter validation failed. |
| 500 | Unexpected internal error (generic message; the cause is logged server-side). |
| 503 | A required dependency (PostgreSQL) is unavailable (readiness probe). |

### Content text

Extracted bodies begin with the marker `TEXT-BEGIN`. The `content_hash` is the SHA-256 hex digest
of the marked text. `has_content` reports whether text is stored.

## Endpoints

### System

| Method | Path | Summary |
| --- | --- | --- |
| GET | `/api/v1/health` | Liveness probe: `{"status":"ok","version":"0.1.0"}`. |
| GET | `/api/v1/ready` | Readiness probe; checks PostgreSQL. `503` when unavailable. |

### Sources

| Method | Path | Summary |
| --- | --- | --- |
| GET | `/api/v1/sources` | List configured sources. |
| GET | `/api/v1/sources/{source_code}` | One source; `404` if unknown. |

### Articles

| Method | Path | Summary |
| --- | --- | --- |
| GET | `/api/v1/articles` | Paginated, filterable list (no bodies). |
| GET | `/api/v1/articles/{article_id}` | One article with body, localizations, resources. |
| GET | `/api/v1/articles/{article_id}/resources` | Resources of one article. |

### Reports

Same shape as articles, under `/api/v1/reports`, `/api/v1/reports/{report_id}`,
`/api/v1/reports/{report_id}/resources`.

### Resources

| Method | Path | Summary |
| --- | --- | --- |
| GET | `/api/v1/resources` | Paginated list with parent reference. |
| GET | `/api/v1/resources/{resource_id}` | One resource; `404` if unknown. |

### Crawl runs

| Method | Path | Summary |
| --- | --- | --- |
| POST | `/api/v1/crawl-runs` | Trigger a crawl (bearer token; `202 Accepted`). |
| GET | `/api/v1/crawl-runs` | Paginated run history with per-source results. |
| GET | `/api/v1/crawl-runs/{run_id}` | One run by UUID. |
| GET | `/api/v1/crawl-runs/{run_id}/errors` | Classified errors for one run. |

### Triggering a crawl

```http
POST /api/v1/crawl-runs
Authorization: Bearer <MANUAL_TRIGGER_TOKEN>
Content-Type: application/json

{ "source": "cba", "max_pages": 2 }
```

- `source` (required): `uae`, `cba`, `commerce`, or `all`.
- `max_pages` (optional, 1–1000): listing-page bound per source; omit to use the configured default.

Response `202 Accepted`:

```json
{ "id": "d871f29b-46bf-42b3-82aa-642ca6dd271b", "status": "running", "trigger_type": "api", "message": "Crawl accepted." }
```

The run is created in `running` status and executed in a background worker. Poll
`GET /api/v1/crawl-runs/{id}` for status (`running` → `success` / `partial_failure` / `failed`).

**Lifecycle:** the trigger reserves a PostgreSQL advisory lock, creates **one** `crawl_runs` row
(`trigger_type = "api"`, status `running`), and returns its id. The worker runs the selected
actors and persists results against the **same** run, then finalizes status and timestamps. A
worker exception still finalizes the run as `failed` with a `worker_error` recorded. No second
run row is created.

**Concurrency:** at most one active execution per source. A duplicate active run for the same
source returns `409`; `all` takes every per-source lock, so it conflicts with any single-source
run. Locks are released on success, failure, and connection loss.

**Background execution limits:** execution is **in-process** and **not durable across
application restarts**. It is intended for a **single application worker**; do not run multiple
API processes against the same database and expect the in-process state to be shared (the
advisory locks are cluster-wide, so a second process's trigger is rejected while a run is active,
but the run's progress lives only in the process that started it).

## Classification of articles vs reports

Classification is **hybrid** and derived from existing stored fields:

1. The stored `document_type` label is normalized and, when recognized, decides the kind
   (`"News Releases"`, `news_article`, `news_release` → article; `report`, `publication` → report).
2. Otherwise the source code decides: `uae_legislation_news` and `cba_news_releases` → article;
   `commerce_reports` → report.

An item never appears under both routes; requesting a report id through `/articles/{id}` returns
`404`, and vice versa.

## Examples

### List articles

```bash
curl "http://localhost:8000/api/v1/articles?source=cba_news_releases&limit=25"
```

```json
{
  "items": [
    {
      "id": 2,
      "kind": "article",
      "source": "cba_news_releases",
      "source_name": "Canadian Bar Association",
      "title": "Gen Z financial literacy program expands its scam prevention training",
      "url": "https://cba.ca/article/gen-z-financial-literacy-program-expands-scam-prevention-training",
      "language": "en",
      "document_type": "News Releases",
      "published_at": "2025-11-03",
      "published_precision": "day",
      "published_year": null,
      "published_month": null,
      "summary": "The Your Money Students program updated to better assist young Canadians spot scams",
      "image_url": "https://cba.ca/Assets/CanadianBankersAssociation/Images/Articles/Banking_You/choosingRightBankAccount2025.png",
      "topics": ["#Fraud", "#Financial Literacy"],
      "has_content": true,
      "content_hash": "6b6be57ce6d7342cdbd55a4e32466096d8afbba797fe92f200cdf67e54ab7c10",
      "first_seen_at": "2025-11-05T08:00:00Z",
      "last_seen_at": "2025-11-05T08:00:00Z",
      "updated_at": "2025-11-05T08:00:00Z"
    }
  ],
  "pagination": { "limit": 25, "offset": 0, "total": 5 }
}
```

### Article detail

```bash
curl "http://localhost:8000/api/v1/articles/2"
```

The response adds `native_id`, `listing_url`, `content` (starting with `TEXT-BEGIN`),
`localizations`, and `resources`.

### Reports

```bash
curl "http://localhost:8000/api/v1/reports?source=commerce_reports"
```

Reports use the same summary/detail shape with `"kind": "report"`.

### Resource

```bash
curl "http://localhost:8000/api/v1/resources/1"
```

```json
{
  "id": 1,
  "document_id": 11,
  "parent": { "id": 11, "kind": "report", "source": "commerce_reports", "title": "…", "url": "…" },
  "identity_key": "commerce_reports:url:…:resource:…",
  "title": "Investing-in-American-Competitiveness.pdf",
  "display_title": null,
  "url": "https://www.commerce.gov/sites/default/files/2025-01/Investing-in-American-Competitiveness.pdf",
  "resource_type": "pdf",
  "mime_type": null,
  "file_size_bytes": null,
  "page_count": null,
  "character_count": null,
  "extraction_status": "access_blocked",
  "has_content": false,
  "content_hash": null,
  "extraction_error": "source refused the request (HTTP 403)",
  "first_seen_at": "2025-11-05T08:00:00Z",
  "last_seen_at": "2025-11-05T08:00:00Z"
}
```

### Crawl run

```bash
curl "http://localhost:8000/api/v1/crawl-runs?limit=3"
```

Returns runs with a `sources` array of per-source results (status, counters, `error_summary`).

## Exporting the OpenAPI schema

The schema is generated by FastAPI (source of truth); do not maintain a hand-written copy.

```bash
uv run python -c "import json; from lexcrawl.api import create_app; print(json.dumps(create_app().openapi(), indent=2))" > openapi.json
```

## Known limitations

- **No scheduler**: crawls run only when triggered through the API or the CLI.
- **In-process, non-durable execution**: triggered crawls run in a background thread of the API
  process and do not survive an application restart; intended for a single API worker. No
  external queue (Celery/Redis/Kafka) is used.
- **Read endpoints are unauthenticated**; only `POST /crawl-runs` requires a bearer token.
- **No `bootstrap` option over the API**: Commerce historical acquisition remains CLI-only
  (`--bootstrap`); the API path runs the standard preflight.
- **No full-text search** (`q`); omitted until a proper index is designed.
- **No resource download**; resources expose metadata and the original remote URL only.
- **Offset pagination** (no cursor).
- `sources.base_url` is currently `null` for all sources.
- `published_from`/`published_to` are approximate for year-precision items (year fallback).
