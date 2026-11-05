# LexCrawl — Persistence Verification Report

- **Date:** 2026-11-05
- **Branch / HEAD:** `main` / `149e2324425fdeaa372df0ecfabc0c62f2059a06`
- **Database:** `postgresql+psycopg://localhost:5432/lexcrawl` (env `local`), container `lexcrawl-postgres` (healthy), separate isolated `lexcrawl_test`
- **Secrets:** none exposed; `.env` not modified; no files reset or deleted; nothing committed

---

## 1. Initial repository and database state

- **Git:** working tree dirty — 16 modified + 4 untracked files (the known stabilization changes plus `AUDIT_REPORT.md`, `IMPLEMENTATION_REPORT.md`, `src/lexcrawl/crawling/rotating.py`, `tests/unit/test_rotating.py`). No reset/clean performed.
- **Alembic before:** current `0005_run_preflight_state`; head `0006_doc_extraction_attempts`.
- **Data before:** `documents=0`, `document_localizations=0`, `document_resources=0`, `crawl_runs=4` (all failed), `crawl_run_sources=4`, `crawl_errors=4`, `sources=3` (seeded: `uae_legislation_news`, `cba_news_releases`, `commerce_reports`).
- **CLI confirmed:** `lexcrawl db {upgrade,current,seed,revision}`; `lexcrawl crawl --source {uae,commerce,cba,all} --max-pages N [--bootstrap] [--persist]`.
- **Target confirmed:** the configured database is the intended local LexCrawl development database (`lexcrawl`), not the isolated `lexcrawl_test`.

## 2. Migration safety assessment

- **Change (additive only):** `ALTER TABLE documents ADD COLUMN extraction_attempts smallint NOT NULL DEFAULT 0`; `ADD CONSTRAINT extraction_attempts_non_negative CHECK (extraction_attempts >= 0)`.
- **ORM compatibility:** exact match — `db/models/document.py:92` (`SmallInteger`, `nullable=False`, `server_default=0`) and `:60` (same check). Write paths (`repositories/documents.py:54-57`, `repositories/mapping.py:153`) and the incomplete-report query (`repositories/documents.py:123`) require the column.
- **Existing records:** none affected (`documents` empty); any non-empty table would receive `0`, satisfying the check. No backfill, no type change, no rewrite.
- **Reversible:** `downgrade()` drops the constraint, then the column.
- **Risk: LOW.**

## 3. Authorization and application

- **Authorized:** the user explicitly approved ("Approve and proceed").
- **Applied:** `uv run lexcrawl db upgrade` → exit 0, `Running upgrade 0005_run_preflight_state -> 0006_doc_extraction_attempts`, message `database upgraded to head`.
- **Revision after:** `0006_doc_extraction_attempts (head)`.
- **No destructive recovery or manual schema edits were performed.**

## 4. Actual schema verification

- **Column:** `information_schema.columns` → `extraction_attempts | smallint | is_nullable=NO | column_default=0`.
- **Constraint:** `pg_constraint` → `ck_documents_extraction_attempts_non_negative` = `CHECK ((extraction_attempts >= 0))` (the naming convention prefixes the name).
- **Existing rows intact:** `documents=0`, `sources=3`, `crawl_runs=4`, `crawl_run_sources=4`, `crawl_errors=4` immediately after the migration.
- **Model/DB consistency:** `alembic check` → `No new upgrade operations detected.` (**PASS**).

## 5. CBA persistence results

Command: `uv run lexcrawl crawl --source cba --max-pages 1 --persist --out /tmp/kilo/cba_persist1.json`

- Run completed with no database errors; `status=partial_failure` only because `--max-pages 1` hit the listing bound (an explicit recorded error), `listing=1/1`, `details=5/5`.
- **Persisted:** `documents=5`, `document_localizations=5`, `document_resources=0`.
- All 5 documents: `extraction_status=complete`, `extraction_attempts=0`, distinct `identity_key`s (`cba_news_releases:id:…`), `source_url` present.
- All 5 localizations (`en`): `text_content LIKE 'TEXT-BEGIN%'` = 5, `content_hash` populated = 5, lengths 2357–5447.
- Crawl history recorded: one `crawl_runs` row with one `crawl_run_sources` row and the run's error.

## 6. Idempotency results

Command (repeat, same scope): `uv run lexcrawl crawl --source cba --max-pages 1 --persist --out /tmp/kilo/cba_persist2.json`

- **No duplication:** `documents` stayed **5**, `document_localizations` stayed **5**, `document_resources` stayed **0**.
- **Upsert confirmed:** documents remain `extraction_status=complete`, `extraction_attempts=0`; content was not replaced by empty/incomplete results (all still `TEXT-BEGIN` with hashes).
- **Run history is separate:** `crawl_runs` 5 → **6**, `crawl_run_sources` 5 → 6, `crawl_errors` 5 → 6 (each execution gets its own run row).

## 7. Commerce verification results

- **Preflight (no credits):** `uv run lexcrawl crawl --source commerce` → fails safe; `check_outcome=check_failed`, `documents=0`; recorded error `change detection fetch failed …: robots.txt disallows …`. **No crash, and no more `ProgrammingError`** (the earlier missing-column symptom is gone). This is an **external access limitation** (Cloudflare 403 on the plain-HTTP listing/robots for this egress); no bypass was attempted.
- **Controlled bootstrap + persist:** the configured detail budget is 20, so a **1-credit** scope was used (`bootstrap=True, max_pages=1, max_detail_scrapes=1`) with the shipped (now-fixed) `HttpFetcher`:
  - `status=partial_failure`, `check_outcome=new_data`, `documents=1` with full `TEXT-BEGIN` text, `pending=19`, `credits=1` (listing page served from the Firecrawl cache).
  - Parent report extracted and persisted: commerce document + `en` localization (`complete`, `TEXT-BEGIN`, content hash, 8561 chars).
  - **Attachment isolation verified:** the linked PDF answered 403 and was stored as `document_resources` row `extraction_status=access_blocked`, `resource_type=pdf`, with `original_url` preserved; the run did not abort.

## 8. Before-and-after database counts

| Stage | documents | localizations | resources | crawl_runs | run_sources | errors |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Before (pre-migration) | 0 | 0 | 0 | 4 | 4 | 4 |
| After migration | 0 | 0 | 0 | 4 | 4 | 4 |
| After CBA run 1 (persist) | 5 | 5 | 0 | 5 | 5 | 5 |
| After CBA run 2 (idempotency) | **5** | **5** | **0** | 6 | 6 | 6 |
| After Commerce bootstrap (persist) | 6 | 6 | 1 | 7 | 7 | 10 |

Explanation: CBA runs added 5 documents/localizations (no resources); the re-run added none (upsert), only new run history. Commerce added 1 document, 1 localization, and 1 `access_blocked` resource. Error rows grow with the recorded per-run conditions (e.g., the max-pages bound, the blocked attachment).

## 9. Automated test results

| Check | Command | Result |
| --- | --- | --- |
| Full suite | `uv run pytest` | **PASS — 491 passed** in 56.77 s |
| Ruff lint | `uv run ruff check .` | **PASS** |
| Ruff format | `uv run ruff format --check .` | **PASS** — 98 files already formatted |
| Mypy | `uv run mypy src` | **PASS** — no issues in 52 source files |
| DB integration | `uv run pytest tests/integration/db` | **PASS — 48 passed** (isolated `lexcrawl_test`) |
| Alembic consistency | `alembic check` | **PASS** — no new upgrade operations (run after the configured DB reached head) |

All commands were actually executed; no check is reported as passed without a run.

## 10. Remaining blockers

- **No critical (P0) blockers.** Migration `0006` is applied; real persistence and idempotency are verified on the configured database.
- **Operational limitation — Commerce preflight (P1-3):** the plain-HTTP preflight is refused by Cloudflare for this egress, so incremental detection reports `check_failed`; acquisition requires the explicit `--bootstrap` path (Firecrawl credits) or an approved egress. Not a code defect.
- **Operational limitation — UAE (P1-4):** collection depends on browser-session rotation due to the site's ~2-request-per-session throttle; verified working (previous run 26/26 with `TEXT-BEGIN`), but slow and sensitive to the site's budget.
- **P3 — uncommitted changes:** all fixes and this verification remain working-tree changes; commit only when requested.

## 11. Final readiness verdict

**PARTIALLY READY.**

The database migration is applied and verified, and real persistence plus idempotency are proven on the configured database for **CBA** and **Commerce (bootstrap)** — documents, localizations, and resources persist with `TEXT-BEGIN` content and hashes, identities are stable, and repeated runs do not duplicate. **No critical blockers remain**, so FastAPI development can proceed against the verified persistence layer. The classification is not the top verdict only because external/operational limitations remain for **Commerce preflight** (Cloudflare-blocked; use `--bootstrap` or an approved egress) and **UAE** (browser-rotation throttling), which are access constraints rather than system defects.
