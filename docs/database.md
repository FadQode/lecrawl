# LexCrawl Database

PostgreSQL persistence for the canonical ingestion output. This phase adds storage only; there is
no API, scheduler, or deployment layer.

## Architecture

```mermaid
erDiagram
    sources ||--o{ documents : owns
    sources ||--o{ crawl_run_sources : participates
    documents ||--o{ document_localizations : has
    documents ||--o{ document_resources : has
    crawl_runs ||--o{ crawl_run_sources : contains
    crawl_run_sources ||--o{ crawl_errors : records
    documents ||--o{ crawl_errors : attributes
    document_localizations ||--o{ crawl_errors : attributes
    document_resources ||--o{ crawl_errors : attributes
```

| Layer | Module | Responsibility |
| --- | --- | --- |
| Configuration | `lexcrawl.config.Settings` | `DATABASE_URL`, pool size/overflow/timeouts, echo |
| Engine | `lexcrawl.db.engine` | Lazy engine, bounded pool, connect timeout, DSN masking |
| Session | `lexcrawl.db.session` | Session factory and `session_scope` unit of work |
| Base | `lexcrawl.db.base` | Declarative base + deterministic constraint naming |
| Models | `lexcrawl.db.models` | ORM entities only (no Pydantic, no actor imports) |
| Repositories | `lexcrawl.repositories` | Canonical → row mapping and conflict-aware upserts |
| Service | `lexcrawl.persistence` | Transactional persistence of `ActorRunResult` / `CrawlRunReport` |
| Migrations | `migrations/` | Alembic revisions `0001`…`0004` |

## Tables

| Table | Rows represent | Key constraints |
| --- | --- | --- |
| `sources` | One configured source (registry) | `UNIQUE(code)` |
| `documents` | One logical document | `UNIQUE(source_id, identity_key)` |
| `document_localizations` | One language variant of a document | `UNIQUE(document_id, language_code)` |
| `document_resources` | One attachment of a document | `UNIQUE(document_id, identity_key)` |
| `crawl_runs` | One crawler invocation | `UNIQUE(run_id)` |
| `crawl_run_sources` | One source outcome within a run | `UNIQUE(crawl_run_id, source_id)` |
| `crawl_errors` | One structured error | at most one of document/localization/resource |

## Identity and idempotency

- `documents.identity_key` is the canonical `identity_key` produced by
  `stable_identity(source, native_id, source_url)`:
  `"<source>:id:<native>"` when a native identifier exists, otherwise
  `"<source>:url:<normalised-url>"`. Content hashes never determine identity.
- `document_resources.identity_key` is the canonical resource identity
  (`<parent>:resource:<url-token | id:<native> | kind:title:index>`), so a resource with a URL is
  identified by that URL, an attachment with a native id by that id, and only otherwise by
  type/title.
- Every write is a PostgreSQL `INSERT … ON CONFLICT … DO UPDATE` on the unique key, so concurrent
  writers converge on one row. `first_seen_at` is set only on insert; `last_seen_at` and
  `updated_at` advance on every observation.
- Re-persisting the same `ActorRunResult` replaces its counters and its error rows instead of
  duplicating them.

## Localizations, resources, and refresh semantics

- The primary document becomes one localization row for `documents.language`; each
  `DocumentTranslation` becomes another row. Arabic and English therefore share one document
  when they share an identity.
- **Latest attempt state and last successful content are separate dimensions.** `status` (and
  `error`) always reflect the newest attempt; `text_content`/`content_hash` and `fetched_at` are
  only replaced by newer content, so a blocked or failed refresh cannot erase the last good text
  (enforced by the *pairing* check, not by forbidding text on non-complete statuses).
- `last_attempt_at` advances on every attempt; `fetched_at` only on a completed one.
- Resources follow the same rules: a failed extraction keeps the previously extracted text and
  hash while recording `extraction_status='failed'` and `extraction_error`.
- URL-less attachments are persisted with `original_url = NULL` and
  `extraction_status='url_unavailable'`; no URL is ever invented.
- One resource's failure never invalidates its parent or its siblings.

## Publication precision

`published_precision` is one of `day`, `month`, `year`, `unknown` with database checks:

- `day` requires `published_at`; `month` requires month+year and no explicit day; `year` requires
  a year only; `unknown` forbids all date parts.
- The canonical contract allows `published_precision = None`; when persisting, an exact
  `published_at` maps to `day` and the absence of any date maps to `unknown`. This inference is
  deterministic and never fabricates a part.
- Commerce month-year values keep `published_at = NULL`; the month and year are stored as-is.

## Transaction boundaries

- One `crawl_runs` row is created first with `status='running'` (its own transaction), so source
  outcomes can reference it.
- **Each source result is persisted in its own transaction.** A failing source is rolled back,
  then recorded as `status='failed'` with a `persistence` error in a separate transaction; other
  sources stay committed.
- The run is finalized afterwards with the aggregate status, `finished_at`, and `duration_ms`.
- A run with an unregistered source code is refused before any write, because no FK target exists.

## Canonical → database mapping

| Canonical field | Column | Transformation | Nullability |
| --- | --- | --- | --- |
| `source` | `sources.code` (via `documents.source_id`) | registry lookup | required |
| `identity_key` | `documents.identity_key` | unchanged | required |
| `native_id` | `documents.native_id` | unchanged | nullable |
| `source_url` | `documents.source_url` | unchanged | required |
| `listing_url` | `documents.listing_url` | unchanged | nullable |
| `type_of_law` | `documents.document_type` | `type_of_law` or per-source default (`news_article`, `news_release`, `report`, `document`) | required |
| `published_at` | `documents.published_at` (`DATE`) | unchanged | nullable |
| `published_month` / `published_year` | `documents.published_month` / `published_year` | unchanged | nullable |
| `published_date_raw` | `documents.published_date_raw` | unchanged | nullable |
| `published_precision` | `documents.published_precision` | `None` → `day` if a date exists, else `unknown` | required |
| `source_created_at` | `documents.source_created_at` | unchanged | nullable |
| `topics` | `documents.topics` (`TEXT[]`) | tuple → array | required (default `{}`) |
| `image_url` | `documents.image_url` | unchanged | nullable |
| `provenance` | `documents.provenance` | unchanged | required (default `live`) |
| `archive_url`, `archived_at`, `archive_listing_url` | same names | unchanged | nullable |
| `extraction_status` | `documents.extraction_status` | unchanged | nullable |
| `extraction_error` | `documents.extraction_error` (+ `crawl_errors` row) | redacted | nullable |
| `title`, `language`, `summary`, `text`, `content_hash` | `document_localizations.*` (primary row) | `text` unchanged (`TEXT-BEGIN`) | `title` nullable for translations, `text`/hash nullable |
| `extraction_status` (primary) | `document_localizations.status` | `None` → `complete` if text, else `pending` | required |
| `translations[]` | additional `document_localizations` rows | one row per language | `language` unique per document |
| `DocumentTranslation.parent_identity` | — | implicit through `document_id` | not stored (derivable) |
| `DocumentResource.title` | `document_resources.title` | unchanged | required |
| `DocumentResource.display_title` | `document_resources.display_title` | unchanged | nullable |
| `DocumentResource.native_id` | `document_resources.native_id` | unchanged | nullable |
| `DocumentResource.url` | `document_resources.original_url` | unchanged | nullable |
| `DocumentResource.kind` | `document_resources.resource_type` | unchanged | required |
| `DocumentResource.text` | `document_resources.extracted_text` | unchanged (`TEXT-BEGIN`) | nullable |
| `DocumentResource.error` | `document_resources.extraction_error` (+ `crawl_errors` row) | redacted | nullable |
| `DocumentResource.status` | `document_resources.extraction_status` | `None` → `pending` | required |
| `DocumentResource.mime_type`, `file_size`, `page_count`, `character_count`, `content_hash`, `archive_url`, `archived_at` | same names (`file_size` → `file_size_bytes`) | unchanged | nullable |
| `DocumentResource.identity_key` / `parent_identity` | `document_resources.identity_key` | unchanged / implicit via `document_id` | required / not stored |
| `ActorRunResult.*` counters | `crawl_run_sources.*` | 1:1, plus `documents_processed = len(documents)` and `documents_failed = detail_pages_failed` | required, non-negative |
| `ActorRunResult.errors` | `crawl_errors` (stage `unknown`, type `actor_error`) | redacted, 2000-char cap | required message |
| `ActorRunResult.documents` | `documents` + localizations + resources | as above | — |
| `RawArticle`, `ArticleStub` | — | pre-canonical intermediates, never persisted | — |

**Intentionally not persisted:** `parent_identity` values (derivable from the parent row), raw
HTML/PDF binaries, and full raw source payloads. No `JSONB` is used: every field the contract
carries has a typed column.

## Configuration

```env
DATABASE_URL=postgresql+psycopg://lexcrawl:lexcrawl@localhost:5432/lexcrawl
DB_POOL_SIZE=5
DB_MAX_OVERFLOW=5
DB_POOL_TIMEOUT=30
DB_CONNECT_TIMEOUT=10
DB_ECHO=false
```

No connection is opened at import time; DSNs are masked in logs and errors, and stored error
messages are redacted for passwords and API keys.

## Operations

```bash
# Development/test PostgreSQL (disposable, image built from docker/postgres/Dockerfile)
docker build -t lexcrawl-postgres:16 docker/postgres
docker run -d --name lexcrawl-postgres -p 5432:5432 lexcrawl-postgres:16

uv run lexcrawl db upgrade          # apply migrations (head)
uv run lexcrawl db current          # show the applied revision
uv run lexcrawl db revision -m msg  # create an empty revision for manual edits
uv run lexcrawl db seed             # idempotently insert the source registry

uv run lexcrawl crawl --source uae --persist   # crawl and persist (JSON output unchanged)
```

## Tests

```bash
uv run pytest tests/integration/db     # PostgreSQL integration tests
uv run pytest                          # everything (DB tests skip when no server is reachable)
```

The suite creates `lexcrawl_test` on the same server (override with
`LEXCRAWL_TEST_DATABASE_URL`), migrates it with Alembic, truncates between tests, and verifies
constraints, cascades, upsert idempotency, refresh preservation, run isolation, concurrency, and
migration round-trips. Unit tests (`tests/unit/test_db_mapping.py`) cover the mapping without a
database.

## Known limitations

- `documents_failed` mirrors `detail_pages_failed`; its unit differs per source (UAE
  localizations, CBA releases, Commerce reports) and is documented on `ActorRunResult`.
- `content_fetched_at` was merged into `fetched_at`: the canonical contract has one "last
  successful fetch" notion, so a second identical column would only duplicate it.
- Source `base_url` is left NULL by the seed (the repository does not hardcode site URLs);
  operators may set it.
- Crawl errors from actors are still plain strings, so their `stage` is `unknown` and their type
  is `actor_error`; document/resource errors are attributed precisely.
- No document version history, retention policy, or API yet (out of scope).
