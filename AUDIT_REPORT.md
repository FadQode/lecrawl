# LexCrawl — Comprehensive System Re-Verification Report

- **Date:** 2026-11-05
- **Branch / HEAD:** `main` / `149e2324425fdeaa372df0ecfabc0c62f2059a06`
- **Working tree:** dirty (post-audit changes present, uncommitted — see §B)
- **Scope:** independent re-verification of the full pipeline, treating all prior audit findings as hypotheses
- **Method:** read-only inspection, deterministic offline repros, isolated test-database checks, live probes
- **Changes made by this audit:** none to source, config, or schema. A test-database probe and throwaway harnesses (`/tmp/kilo/*`, outside the repo) were used. No migration was applied; the configured database was not modified or reset.

> Secrets are not reproduced. `.env` is git-ignored and untracked; values are reported only as `<set>`.

---

## A. Executive Summary

**Overall verdict: PARTIAL.** The architecture, canonical contract, validation, mapping, repositories, and persistence layer are sound and well tested (469 tests, ruff, ruff-format, and mypy strict all pass). The UAE source now collects **full article text** end-to-end through automatic browser-session rotation (verified live, 26/26). PDF extraction is correct. But **the system is not ready for API development**: two independent P0 blockers prevent reliable, repeated ingestion, and the API and scheduler are unimplemented.

**Critical blockers: 2**
1. `HttpFetcher` double-decodes gzip/deflate responses (`httpx.DecodingError`). Every plain-HTTP source fails live (CBA, Commerce), and Commerce `--bootstrap` aborts when a report links a PDF.
2. Migration `0006_doc_extraction_attempts` is not applied to the configured database, so every document write fails (`UndefinedColumn`); the configured database holds **0 documents**.

**Major issues: 4** (P1) — see §H.
**Minor issues: 5** (P2/P3) — see §H.

**Ready for API development? NO.** Fix P0-1 and P0-2, then re-run the acceptance checklist (§I). The API/scheduler stubs must also be built, but they are expected gaps, not defects.

---

## B. Repository State

| Item | Value |
| --- | --- |
| Branch | `main` |
| HEAD commit | `149e2324425fdeaa372df0ecfabc0c62f2059a06` — "feat: add firecrawl for more reliable Commerce scraping" (2026-11-03) |
| Working tree | dirty; see below |
| Python | 3.12.15 (`.python-version` = 3.12), `uv` 0.13.6 |
| Virtualenv | `.venv` present (editable install) |
| Docker | `lexcrawl-postgres` Up (healthy), `0.0.0.0:5432->5432/tcp` |
| Configured DB revision | `0005_run_preflight_state` |
| Migration head | `0006_doc_extraction_attempts` |
| Agent guide | `.agents/AGENTS.MD` (450 lines) |

**Uncommitted changes (from the prior exchange, unverified by Git history):**
```
 M .env.example
 M README.md
 M src/lexcrawl/cli.py
 M src/lexcrawl/config.py
 M tests/unit/test_cli.py
?? AUDIT_REPORT.md
?? src/lexcrawl/crawling/rotating.py
?? tests/unit/test_rotating.py
```

**Configuration / environment keys (values masked).** `.env` defines 57 keys including `DATABASE_URL`, `CRAWL_ENGINE`, `UAE_CRAWL_ENGINE` (defaulted, not set), `LISTINGS_URL`, `RELEASES_*`, `REPORTS_*`, `FIRECRAWL_API_KEY`, `REPORTS_API_KEY`. `DATABASE_URL` points at the local Docker PostgreSQL. No secret appears in any tracked file (`git grep` for the key literal returns nothing; `.env` is not tracked).

**Dependencies** (`requirements.txt`): fastapi, uvicorn, pydantic, pydantic-settings, python-dotenv, SQLAlchemy, psycopg[binary], alembic, httpx, beautifulsoup4, lxml, playwright, PyMuPDF, tenacity, APScheduler, requests. Dev (`requirements-dev.txt`): pytest, pytest-asyncio, respx, coverage, ruff, mypy.

---

## C. Architecture Assessment

The implemented pipeline is a modular monolith with in-process actors. The traced flow is fully connected for the three sources:

```
CLI (cli.py: main/build_actor/build_actors/build_fetcher)
  -> Orchestrator (orchestrator.py: run_crawl)
     -> Actor (actors/uae_legislation.py | cba.py | commerce.py) implements actors/base.Actor
        -> Fetcher (crawling/http.HttpFetcher | crawling/browser.BrowserFetcher
                    | crawling/rotating.RotatingFetcher | crawling/firecrawl.FirecrawlClient)
        -> Discovery -> Parsing -> detail extraction -> CanonicalDocument (models/document.py)
        -> ActorRunResult (actors/base.py) with counters + check_outcome
  -> Validation (validation/{base,uae,cba,commerce}.py, Pydantic)
  -> Mapping (repositories/mapping.py) -> repositories/{documents,localizations,resources,crawl_runs}.py
  -> Persistence service (persistence.py: persist_crawl_report)
  -> PostgreSQL (db/models/*, migrations 0001..0006)
```

- **Actor contract** (`actors/base.py`): abstract `run(fetcher) -> ActorRunResult`; ~21 counters; `check_outcome` (`no_change`/`new_data`/`check_failed`); derived `status`; `requires_http_engine` gating for API sources (`orchestrator.py:82-90`).
- **Canonical model** (`models/document.py`): `CanonicalDocument`, `RawArticle`, `DocumentResource`; deterministic `stable_identity`; `ensure_text_marker` prepends `TEXT-BEGIN`; SHA-256 `compute_content_hash`.
- **Repositories**: idempotent upserts via PostgreSQL `ON CONFLICT`; content never replaced by empty attempts; run/error attribution.
- **Dead or incomplete code:** `api/__init__.py`, `api/routes/__init__.py`, `scheduler/__init__.py` are empty; `tests/api/` contains only `__init__.py` (0 tests). No duplicated responsibilities were found; source-specific selectors stay inside actors, DB access stays inside repositories (actors take a session via the persistence service).
- **Unused declared dependencies:** `requests`, `apscheduler`, `fastapi`, `uvicorn` are declared but not imported anywhere in `src/` (they are for the unimplemented API/scheduler).

## D. Previous Audit Verification

All prior findings were re-examined against the current working tree. Status terms: Confirmed / Fixed / Regressed / Not reproducible / Resolved.

| Previous Finding | Current Status | Evidence | Remaining Action |
| --- | --- | --- | --- |
| HTTP gzip double-decoding | **Confirmed** | Offline repro (`HttpFetcher._request_once`, `crawling/http.py:276-293`): gzip → `DecodingError`, deflate → `DecodingError`, plain → OK. Live: `crawl --source cba` and `--source commerce` both `status=failed documents=0` with `DecodingError: Error -3 while decompressing data`. `httpx.DecodingError` is not caught (only `TimeoutException`/`TransportError`). | Fix in P0-1 |
| Missing migration 0006 | **Confirmed** | `alembic heads` = `0006`; `alembic current` = `0005`; `information_schema` shows no `documents.extraction_attempts`; `alembic check` → `CommandError: Target database is not up to date`. | Apply in P0-2 (approval required) |
| UAE Cloudflare restriction (plain HTTP) | **Confirmed** | `curl https://uaelegislation.gov.ae/ar/news` → `403`. | Not client-fixable |
| UAE collection (browser) | **Resolved for collection** | Live `crawl --source uae --max-pages 3`: `status=success`, `listing=2/2`, `details=26/26`, `discovered=26`, **26 rows all with `TEXT-BEGIN` + hash, 0 metadata-only**, 14 sessions, ~88 s. | None (works) |
| CBA pipeline (logic) | **Confirmed working** | Live via corrected transport: `documents=5`, all 5 with `TEXT-BEGIN`; persisted to isolated test DB twice → still 5 docs (idempotent); SQL `localizations LIKE 'TEXT-BEGIN%'` = 5. Shipped transport fails (P0-1). | Fix P0-1 |
| Commerce preflight restriction | **Confirmed** | Live preflight via corrected transport → HTTP `403` "Just a moment..." → `check_failed`, `credits=0`. Ship transport crashes earlier with `DecodingError`. | Fix P0-1; decide access strategy |
| Commerce bootstrap acquisition | **Confirmed functional (logic)** but **blocked on shipped transport** | With corrected transport: `--bootstrap` → `check_outcome=new_data`, `documents=1 with_text=1`, `discovered=20`, `credits=1`. With shipped transport: aborts in `_process_resource` → `_fetch_cached` → `DecodingError` (uncaught) when a report links a PDF. | Fix P0-1; contain `httpx.HTTPError` in `_process_resource` |
| PDF extraction | **Not regressed** | 12 PDF unit tests pass; offline probe: valid 2-page PDF → `complete`, pages 2, `TEXT-BEGIN`, hash; bad signature/empty/oversized → `failed`; `extract_pdf_text` raises `PdfExtractionError`. Live PDF attachments answer 403 (stored `access_blocked`). | None |
| Missing compressed-response tests | **Confirmed missing** | `grep -rniE 'content-encoding|gzip|deflate|decodingerror' tests/` → no matches. | Add in P1-1 |
| Configured DB has 0 documents | **Confirmed** | `documents=0, crawl_runs=4 (all failed), crawl_run_sources=4, crawl_errors=4, sources=3`. | Fix P0-1 + P0-2, then backfill |
| `--persist` never validated on configured DB | **Confirmed** | Probe `persist_crawl_report` → `UndefinedColumn: column "extraction_attempts" ... does not exist` (probe row removed). | Apply 0006 |
| `data/*.json` artifacts stale | **Confirmed** | Historical files omit `identity_key`/null fields; current `model_dump` emits them. | P3 housekeeping |
| Actor run errors unstructured | **Confirmed** | Errors persist as free strings; `error_type=actor_error`, `stage=unknown` (e.g., `"unexpected actor phase failure: DecodingError(...)"`). | P2-2 |
| Apify implementation absent | **Confirmed** | `grep -rni apify src tests migrations pyproject.toml requirements*.txt` → none. In-process actors only. | None (not required) |
| Secrets untracked | **Confirmed** | `.env` git-ignored, never committed (`git log -- .env` empty). | None |

**New findings this pass:** (a) Commerce `--bootstrap` aborts on any report with a PDF attachment because `_process_resource` (`actors/commerce.py:748-786`) catches `FetchError`/`ValueError` but not `httpx.DecodingError`; (b) two functions lack a `###` docstring and one comment does not end with `$` (see §H P3-1); (c) four declared dependencies are unused.

---

## E. Source Verification Matrix

`PASS` = verified working; `PARTIAL` = works with limitations; `FAIL` = verified broken; `NOT VERIFIED` = insufficient access/evidence. Persistence/Idempotency were verified on the isolated `lexcrawl_test` database (configured DB is blocked by P0-2).

| Source | Discovery | Fetch | Extraction | Validation | Persistence | Idempotency | Overall |
| --- | --- | --- | --- | --- | --- | --- | --- |
| UAE Legislation | PASS (browser) | PASS (rotation) | PASS (26/26 text) | PASS | PARTIAL (test DB PASS; configured FAIL) | PASS (keyed by canonical URL) | **PARTIAL** |
| CBA | PASS (API 200) | FAIL shipped / PASS corrected | PASS (5/5 text) | PASS | PASS (test DB) | PASS (5 rows after 2 runs) | **PARTIAL** |
| Commerce | PASS (Firecrawl listing 20/112) | PARTIAL (bootstrap aborts on PDF, shipped; PASS corrected) | PASS (Firecrawl detail) | PASS | NOT VERIFIED live (logic PASS on test harness) | NOT VERIFIED | **PARTIAL** |

Cross-cutting: Discovery PASS; Fetch is gated by the P0-1 transport defect for all HTTP paths. No source is FAIL for architecture — the failures are transport (P0-1) and schema (P0-2), plus external Cloudflare restrictions that are correctly reported rather than bypassed.

## F. Database Assessment

**Migration status.** Head is `0006_doc_extraction_attempts`; the configured database reports `0005_run_preflight_state`. `alembic check` against the configured DB fails with `CommandError: Target database is not up to date`. Migration `0006` is present in the repo and does add `documents.extraction_attempts` (verified on the migrated test DB: the column exists and `alembic_version` = `0006`).

**Schema consistency.** The SQLAlchemy models and migrations 0001–0006 are faithful: a fresh `lexcrawl_test` migrated to head passes `tests/integration/db/test_schema.py`. Verified constraints include `documents UNIQUE (source_id, identity_key)`, per-table CHECK rules (`num_nonnulls(document_id, localization_id, resource_id) <= 1`, non-negative counters, publication-precision rules), and the descending indexes on `published_at`, `last_seen_at`, and `error occurred_at`.

**Write paths vs real schema.** `repositories/mapping.py:120` and `repositories/documents.py:54-57` unconditionally include `extraction_attempts`. Against the configured `0005` schema this yields `psycopg.errors.UndefinedColumn` on every write; against the `0006` test schema it succeeds.

**Configured database contents (unchanged):** `documents=0`, `document_localizations=0`, `document_resources=0`, `crawl_runs=4` (all `failed`), `crawl_run_sources=4`, `crawl_errors=4`, `sources=3`. All four runs carry the transport `DecodingError`; **nothing was ever persisted.**

**Persistence tests (isolated `lexcrawl_test`):** `tests/integration/db` → **48 passed** (persistence 27, schema 21). Real-data idempotency: a CBA run persisted twice produced `documents=5, localizations=5, resources=0, with_text=5, crawl_runs=2` — no duplicates, one run row per persist.

**Required command (not executed):** `uv run lexcrawl db upgrade` applies pending migration `0006`. Implication: adds the `documents.extraction_attempts` column (and resets it to a real column on the model) so document writes succeed. This is a schema change and requires approval.

---

## G. Test Results

| Check | Command | Result | Exit |
| --- | --- | --- | --- |
| Full suite | `uv run pytest -p no:cacheprovider` | **469 passed** in 42.93 s | 0 |
| Unit | `uv run pytest tests/unit` | 416 passed | 0 |
| Integration | `uv run pytest tests/integration` | 53 passed | 0 |
| DB integration | `uv run pytest tests/integration/db` | 48 passed | 0 |
| API tests | `uv run pytest tests/api` | **no tests ran** | 5 |
| Ruff lint | `uv run ruff check .` | All checks passed | 0 |
| Ruff format | `uv run ruff format --check .` | 97 files already formatted | 0 |
| Mypy (strict) | `uv run mypy` | Success: no issues in 52 source files | 0 |
| Migration consistency | `alembic check` (configured DB) | `CommandError: Target database is not up to date` | 1 |

**Comparison with the previous audit (460 passing):** now **469** (+9). The increase is the post-audit session-rotation work: 6 tests in `tests/unit/test_rotating.py` plus 3 CLI engine-selection tests in `tests/unit/test_cli.py`. No test regressed.

**Deterministic offline checks executed (not mocks of the target service):**
- Compressed-response probe (`/tmp/kilo/gzip_repro.py`): identity OK; gzip/deflate → `DecodingError`.
- PDF probe (`/tmp/kilo/pdf_probe.py`): valid → complete/2 pages/`TEXT-BEGIN`/hash; bad signature, empty, oversized → failed; failure raises `PdfExtractionError`.

**Live probes executed:**
- `curl` UAE `/ar/news` → `403`; Commerce listing → `403`; Commerce `robots.txt` → `403`; CBA `AdvContentRequest` POST → `200`.
- `crawl --source uae --max-pages 3` (browser + rotation) → `status=success`, 26/26 details with `TEXT-BEGIN`.
- `crawl --source cba --max-pages 1` (shipped transport) → `status=failed`, `DecodingError`.
- `crawl --source commerce` (shipped transport) → `status=failed`, `DecodingError`.
- Commerce `--bootstrap` harness: shipped transport aborts at PDF fetch; corrected transport → `new_data`, 1 doc with text (1 credit).
- CBA real-data persistence on test DB (corrected transport): 5 docs, idempotent.

No test was modified or skipped to pass.

## H. Remaining Issues

### P0 — Critical blockers

**P0-1 — `HttpFetcher` decodes compressed responses twice.**
- *Description:* `_request_once` streams `response.iter_bytes()` (httpx gunzips) and then rebuilds an `httpx.Response` reusing the original headers, still including `content-encoding: gzip`. httpx decodes a second time.
- *Evidence:* `src/lexcrawl/crawling/http.py:276-293`. Offline probe: identity OK; `gzip` → `DecodingError: Error -3 while decompressing data: incorrect header check`; `deflate` → `DecodingError: invalid code lengths set`. Live `crawl --source cba` and `--source commerce` → `status=failed documents=0`, `DecodingError`. Only `TimeoutException`/`TransportError` are caught, so `httpx.DecodingError` escapes as an untyped error.
- *Impact:* all plain-HTTP acquisition fails (CBA, Commerce); Commerce `--bootstrap` aborts at the attachment step (see P1-2). No live HTTP record can be collected.
- *Remediation:* strip `content-encoding` and `content-length` when rebuilding the buffered response (or use `iter_raw()` + explicit decode); wrap `httpx.HTTPError` as a typed `FetchError`. Add regression tests (P1-1).
- *Blocks API:* **Yes.**

**P0-2 — Migration `0006_doc_extraction_attempts` is not applied to the configured database.**
- *Description:* ORM writes include `documents.extraction_attempts`, but the configured DB is at `0005` and lacks the column.
- *Evidence:* `alembic current` = `0005_run_preflight_state`; `alembic heads` = `0006`; `information_schema` → no column; `alembic check` → `Target database is not up to date`; persistence probe → `psycopg.errors.UndefinedColumn`. Configured DB: `documents=0`.
- *Impact:* every document write fails; no data has ever been persisted; the deliverable's persistence claim is unproven on the real database.
- *Remediation:* `uv run lexcrawl db upgrade` (applies `0006`). Consider a pre-`--persist` guard that fails fast when `alembic current != head`.
- *Blocks API:* **Yes.**

### P1 — Major issues

**P1-1 — No tests for compressed responses.**
- *Description:* the transport defect is invisible to the suite.
- *Evidence:* `grep -rniE 'content-encoding|gzip|deflate|decodingerror' tests/` → no matches; `respx` fixtures return uncompressed bodies.
- *Impact:* the bug shipped and green-lit 460 tests.
- *Remediation:* add tests asserting a single decode for `gzip`/`deflate` and a typed `FetchError` for a malformed encoding.
- *Blocks API:* No (but gates P0-1 acceptance).

**P1-2 — HTTP errors are not consistently converted to typed exceptions (Commerce resource path).**
- *Description:* `_process_resource` catches `AccessBlockedError`/`AccessDeniedError`, `FetchError`, and `ValueError`, but not `httpx.DecodingError`; the exception propagates and aborts the run.
- *Evidence:* `src/lexcrawl/actors/commerce.py:748-786`. Live bootstrap crash path: `commerce.py:389 run -> 609 _acquire -> 632 _acquire_one -> 652 _store_report -> 702 _process_resources -> 755 _process_resource -> http.py:183 _fetch_cached -> DecodingError`.
- *Impact:* a single attachment response kills the whole Commerce bootstrap run even when Firecrawl acquisition succeeded.
- *Remediation:* reclassify `httpx.HTTPError` at the transport boundary (P0-1) so callers only see typed exceptions; add a catch-all `FetchError` at the resource boundary.
- *Blocks API:* **Yes** (via P0-1), and independently weakens run isolation.

**P1-3 — Commerce incremental preflight is permanently refused from this egress.**
- *Description:* the cheap plain-HTTP preflight that distinguishes `no_change`/`new_data` is answered with Cloudflare `403` ("Just a moment..."); only `--bootstrap` acquires.
- *Evidence:* preflight probe → `check_failed`, `credits=0`; `curl` listing → `403`.
- *Impact:* routine incremental ingestion is impossible; every run must use a manual bootstrap flag and spend Firecrawl credits.
- *Remediation:* decide the access strategy (route preflight through Firecrawl, accept bootstrap-only, or run from an allowed network). No client-side bypass.
- *Blocks API:* No (but blocks reliable scheduled ingestion).

**P1-4 — Verified state is uncommitted; no reproducible baseline in Git.**
- *Description:* the session-rotation and per-source engine changes are working-tree changes; HEAD is still `149e232`, whose suite reports 460 tests.
- *Evidence:* §B `git status` and `git log`. The 469-test baseline is not reproducible from a commit.
- *Impact:* the verified artifact cannot be handed to reviewers or CI reproducibly.
- *Remediation:* commit the reviewed changes (only when authorized).
- *Blocks API:* No.

### P2 — Minor issues

- **P2-1 — Unused declared dependencies.** `requests`, `apscheduler`, `fastapi`, `uvicorn` are in `requirements.txt` but imported nowhere in `src/`. Impact: larger images, ambiguous intent. Remediation: remove or move to the API/worker phase.
- **P2-2 — Coarse error taxonomy.** Actor errors persist as free strings (`error_type=actor_error`, `stage=unknown`), e.g. `"unexpected actor phase failure: DecodingError(...)"`. Impact: poor API error representation and filtering. Remediation: map to structured stage/type/retryable.
- **P2-3 — Stale `data/*.json` artifacts.** Historical files omit `identity_key`/null fields. Impact: misleading if read as current output. Remediation: refresh or label as historical.
- **P2-4 — API and scheduler unimplemented.** `api/` and `scheduler/` are empty; `tests/api/` has 0 tests. Expected (this is the next phase) but must be built for the stated goal.
- **P3-1 — Convention exceptions.** Two functions lack a `###` docstring: `src/lexcrawl/crawling/http.py:45` (`filter`) and `src/lexcrawl/crawling/browser.py:98` (`guard_navigation`). One comment does not end with `$`: `src/lexcrawl/persistence.py:124`. Everything else conforms (AST/tokenize scan across `src/` and `migrations/`).

---

## I. Final Acceptance Checklist

Before API implementation begins, all must be true:

- [ ] **P0-1** fixed: compressed responses decode exactly once; malformed encodings raise a typed `FetchError`.
- [ ] **P1-1** added: regression tests cover gzip, deflate, and malformed `content-encoding`.
- [ ] **P0-2** resolved: `alembic current == head` on the configured database (`lexcrawl db upgrade`), with approval.
- [ ] Persistence proven on the configured database: a real run with `--persist` leaves non-zero `documents`/`localizations` rows.
- [ ] Re-running that crawl creates **no duplicates** (same counts).
- [ ] **P1-2** reclassified: resource/attachment failures are contained and recorded, not fatal.
- [ ] **P1-3** decided: Commerce ingestion path agreed (Firecrawl preflight or documented bootstrap-only).
- [ ] **P1-4** addressed: reviewed changes committed; the suite count is reproducible from the commit.
- [ ] `uv run pytest`, `ruff check`, `ruff format --check`, `mypy`, and `alembic check` all green.
- [ ] All three sources produce valid, normalized, persisted documents retrievable by SQL.

---

## J. Recommended Next Actions (minimal, ordered)

1. **Fix P0-1** in `crawling/http.py` (strip `content-encoding`/`content-length` on rebuild; wrap `httpx.HTTPError` as `FetchError`). Add the P1-1 regression tests. Re-run CBA/Commerce live.
2. **Request approval and apply P0-2** (`uv run lexcrawl db upgrade`), then re-run a `--persist` crawl for CBA and confirm SQL-visible rows and idempotency.
3. **Contain P1-2**: ensure `_process_resource` can never abort a run on an untyped HTTP error.
4. **Decide P1-3** (Commerce access strategy) and document the chosen path.
5. **Commit P1-4** once the above is verified, so reviewers reproduce the 469-test (or higher) baseline.
6. Address **P2/P3** opportunistically (unused deps, error taxonomy, stale artifacts, two docstrings / one comment).
7. Only then start FastAPI development (the empty `api/`/`scheduler/` modules), building on the proven persistence and crawl-run tracking.

---

## Final decision

**NO-GO for API development until P0-1 and P0-2 are fixed and the persistence acceptance checklist passes.** The underlying architecture, canonical model, validation, mapping, repositories, PDF pipeline, and UAE browser rotation are sound and verified; the blockers are a localized transport defect and an unapplied migration, not architectural problems.
