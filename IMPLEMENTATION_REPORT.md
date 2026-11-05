# LexCrawl — Critical Fixes and Pre-API Stabilization — Implementation Report

- **Date:** 2026-11-05
- **Branch / HEAD:** `main` / `149e2324425fdeaa372df0ecfabc0c62f2059a06`
- **Scope:** fix the pre-API blockers and P1–P3 issues from the 2026-11-05 re-verification
- **Not done (by instruction):** no migration applied, no configured-DB change, no commit, no API/scheduler work
- **Secrets:** none exposed; `.env` untouched

---

## A. Changes implemented

### P0-1 — HTTP response decompression

- **Files:** `src/lexcrawl/crawling/http.py`; tests `tests/unit/test_http.py`.
- **Root cause:** `_request_once` read `response.iter_bytes()` (httpx had already decompressed the body) and then rebuilt an `httpx.Response` reusing the original headers, which still carried `content-encoding: gzip`/`deflate`. httpx decoded a second time → `httpx.DecodingError`. Only `TimeoutException`/`TransportError` were caught, so `DecodingError` escaped untyped.
- **Fix:**
  1. Added `_buffered_response()` which rebuilds the response from the decoded body and **drops `content-encoding` and `content-length`** (httpx recomputes the length to match the decoded bytes). Also added the `_DECOMPRESSED_HEADERS` set.
  2. Added `except httpx.HTTPError` after the timeout/transport handlers, converting malformed responses (bad compression, protocol failures) into a typed `FetchError("invalid HTTP response: ...")`, chaining the cause (`from error`). The identity path is unchanged.
- **Architecture preserved:** the public `Fetcher`/`FetchedPage` contract is unchanged; only the internal reconstruction was corrected.
- **Tests added (7):** gzip decodes exactly once; deflate decodes exactly once; unadvertised compressed body is returned verbatim; rebuilt response drops stale `content-encoding` and recomputes `content-length`; malformed gzip → typed `FetchError` (no retry); malformed deflate → typed `FetchError`; transport error → `TransientFetchError` and retried. (P1-1 coverage.)

### P1-2 — Commerce attachment failure isolation

- **Files:** `src/lexcrawl/actors/commerce.py`; tests `tests/unit/test_commerce.py`.
- **Root cause:** `_process_resource` caught `AccessBlockedError`/`AccessDeniedError`, `FetchError`, and `ValueError`, but not `httpx.DecodingError`; an attachment decoding failure propagated and aborted the whole run.
- **Fix:** added `except httpx.HTTPError` in `_process_resource`, recording the resource as `failed` with message `"attachment fetch failed: ..."` and a warning log; the parent report and sibling attachments continue. Successful extraction and persistence are untouched.
- **Tests added (2):** one malformed attachment + one good PDF → run completes, report body kept, bad resource `failed`, good resource `complete` (and counted), error recorded; all attachments malformed → report still produced, both resources `failed`, no extraction.

### P1-1 — Compression test coverage

Covered by the 7 new tests in `tests/unit/test_http.py` (deterministic, offline, respx + `httpx.ByteStream`), covering identity, valid gzip/deflate, unadvertised bodies, malformed gzip/deflate, and transport errors.

### P1-3 — Commerce access strategy

- **Files:** `README.md` (documentation only; no strategy change).
- **Outcome:** documented the measured behavior (preflight → Cloudflare 403 → `check_failed`, zero credits), the supported `--bootstrap` acquisition path (verified: `new_data`, 1 report with `TEXT-BEGIN` text, 1 credit), and the reviewed code paths. No bypass, stealth, or challenge solving added; the default strategy is unchanged. The recommended operational configuration is an approved egress for the incremental preflight, or explicit `--bootstrap` runs.

### P1-4 — UAE session rotation review

- **Files:** `tests/unit/test_uae_actor.py` (test added); implementation reviewed (`src/lexcrawl/crawling/rotating.py`).
- **Review:** rotation triggers only on `AccessBlockedError`; it closes the current session, opens a fresh one, retries the same request; bounded by `browser_max_sessions` (50) and `browser_blocked_session_limit` (3) with a success resetting the consecutive-block counter; `close()` releases the live session. No duplicate documents: the actor processes each article URL once and identity-keyed persistence is unchanged.
- **Test added (1):** the UAE actor run through a `RotatingFetcher` whose first session blocks the first article — the crawl resumes on a second session, yields 20 documents with unique `identity_key`s and `TEXT-BEGIN` text, and the blocked URL is fetched exactly once on the fresh session.

### P2 — Error classification

- **Files:** `src/lexcrawl/repositories/mapping.py`; tests `tests/unit/test_db_mapping.py`, `tests/integration/db/test_persistence.py`.
- **Change:** added `classify_run_error()` and `_RUN_ERROR_MARKERS`, mapping the actors' existing error phrasing to the **existing** `crawl_errors.stage` vocabulary (`discovery`, `fetch`, `parse`, `extract`, `persistence`, `unknown`) and a small set of categories that match real failure modes (`fetch_error`, `parse_error`, `discovery_error`, `budget_error`, `resource_error`, `extraction_error`, `persistence_error`, and the generic `actor_error` fallback). `run_error_payloads()` now uses it. No schema change; unmapped messages keep `unknown`/`actor_error`.
- **Tests:** 12-case parametrized classifier test; updated the run-error payload test and the persistence error-set expectation.

### P2 — Dependency review

- **Files:** none (decision only).
- **Decision:**
  - `fastapi`, `uvicorn`, `apscheduler` — **intentionally reserved** for the upcoming API (`api/`) and scheduler (`scheduler/`) phases; kept.
  - `requests` — not imported anywhere in `src/`; `httpx` covers all HTTP needs. It is redundant today. Per instructions it was **not removed**; it should be removed or justified during the API phase.
  - No dependency was added or removed.

### P3 — Project conventions

- **Files:** `src/lexcrawl/crawling/http.py` (added `###` docstring to `_RedactHttpxRequestUrls.filter`), `src/lexcrawl/crawling/browser.py` (added `###` docstring to the nested `guard_navigation`), `src/lexcrawl/persistence.py` (comment now ends with `$`).
- **Related sweep:** an AST/tokenize scan of `src/`, `migrations/`, and `tests/` found one further function missing a `###` docstring (`tests/unit/test_uae_bilingual.py` nested `fail_one`), now fixed. One remaining flagged item is the mypy pragma `# type: ignore[no-untyped-def]` in `tests/unit/test_commerce_security.py:48`; it is a tooling directive that must remain syntactically exact and is not a prose comment, so it is intentionally left unchanged.

## B. Verification results

| Check | Result | Notes |
| --- | --- | --- |
| Full pytest | **PASS** | `uv run pytest` → **491 passed** in 43.98 s (was 469; +22 new tests) |
| Ruff check | **PASS** | `uv run ruff check .` → All checks passed |
| Ruff format | **PASS** | `uv run ruff format --check .` → 97 files already formatted |
| Mypy | **PASS** | `uv run mypy src` → Success: no issues found in 52 source files |
| HTTP compression tests | **PASS** | `tests/unit/test_http.py` → 28 passed (7 new compression/transport tests) |
| Commerce attachment isolation | **PASS** | `tests/unit/test_commerce.py` → 38 passed (2 new isolation tests) |
| UAE rotation tests | **PASS** | `tests/unit/test_rotating.py` 6 passed; `tests/unit/test_uae_actor.py` 14 passed (1 new) |
| DB integration tests | **PASS** | `tests/integration/db` → 48 passed (isolated `lexcrawl_test` database) |
| Migration consistency | **EXPECTED FAIL** | `alembic current` = `0005`; `alembic heads` = `0006`; `alembic check` → "Target database is not up to date" (migration intentionally **not** applied) |

**Additional executed evidence (offline and live):**

- Offline compression repro (`/tmp/kilo/gzip_repro.py`, exercises the real `HttpFetcher._request_once`): identity OK; **gzip OK**; **deflate OK**; mislabeled-gzip → `FetchError: invalid HTTP response: ...`.
- Live CBA (`uv run lexcrawl crawl --source cba --max-pages 1`, shipped transport): `status=partial_failure documents=5`, `listing=1/1 details=5/5` — the previously broken path now works.
- Live Commerce preflight (`uv run lexcrawl crawl --source commerce`): fails **safe** (no `DecodingError` crash); `check_failed`. It also surfaces the P0-2 symptom: `could not load incomplete reports: ProgrammingError` because the configured DB lacks `documents.extraction_attempts`.
- Live Commerce `--bootstrap` (corrected-transport harness): `check_outcome=new_data`, 1 document with `TEXT-BEGIN` text, 1 credit — path unchanged by this task.

**Isolated test database `lexcrawl_test`:** the DB integration suite migrates it to head `0006`; persistence and idempotency tests pass there. The **configured** database was not touched.

---

## C. Remaining blockers

- **P0-2 — migration `0006` not applied (the only remaining P0).** Unresolved **by instruction** ("Do not apply the migration in this implementation task"). Until it is applied, every document write on the configured database fails with `UndefinedColumn`, and the Commerce actor's incomplete-report load raises `ProgrammingError`. It is fully prepared and safe to apply (see §D).
- **P1-3 — Commerce preflight external restriction (not a code defect).** Plain-HTTP access to the Commerce listing is refused with Cloudflare 403 for this egress, so the incremental preflight reports `check_failed`. This is documented; no bypass is implemented. Resolution requires an approved egress or the explicit `--bootstrap` path.
- **P1-4 — post-audit changes remain uncommitted.** The rotation and this task's fixes are working-tree changes; the 491-test baseline is not reproducible from a commit until they are committed (only when explicitly requested).

No other P0/P1 issue remains. P0-1 and P1-1/P1-2 are fixed and verified; P2/P3 items are addressed or intentionally deferred with rationale.

---

## D. Database migration readiness

- **Current configured DB revision:** `0005_run_preflight_state`.
- **Expected head:** `0006_doc_extraction_attempts`.
- **Exact schema changes in `0006` (`migrations/versions/0006_doc_extraction_attempts.py`):**
  - `upgrade()`: `ALTER TABLE documents ADD COLUMN extraction_attempts smallint NOT NULL DEFAULT 0`, then add `CONSTRAINT extraction_attempts_non_negative CHECK (extraction_attempts >= 0)`.
  - `downgrade()`: drop the check constraint, then drop the column.
- **Consistency:** matches the ORM exactly — `db/models/document.py:92` declares `extraction_attempts` as `SmallInteger, nullable=False, server_default=text("0")`, and `document.py:60` declares the same check constraint. The write paths (`repositories/mapping.py:153`, `repositories/documents.py:54-57`) expect the column; it fails only because it is absent on the configured DB.
- **Safety:** additive and defaulted. Existing rows (`documents` is currently empty; 0 rows) receive `0` and satisfy the check constraint. No data loss, no backfill, no column type change. Downgrade is clean.
- **Exact command (NOT executed):** `uv run lexcrawl db upgrade`
- **Implication:** applying it lets document/localization/resource writes and the Commerce incomplete-report query succeed on the configured database. Persistence must then be re-verified with a real `--persist` run (separate step).

---

## E. Git status

Working tree (no commit made, no reset performed):

**Modified — pre-existing before this task (from the earlier rotation/engine work):**
- `.env.example`, `src/lexcrawl/cli.py`, `src/lexcrawl/config.py`, `tests/unit/test_cli.py`

**Modified — in this task:**
- `src/lexcrawl/crawling/http.py` (P0-1)
- `src/lexcrawl/actors/commerce.py` (P1-2)
- `src/lexcrawl/repositories/mapping.py` (P2 error classification)
- `src/lexcrawl/crawling/browser.py`, `src/lexcrawl/persistence.py` (P3 conventions)
- `README.md` (P1-3 documentation; also carried earlier edits)
- `tests/unit/test_http.py`, `tests/unit/test_commerce.py`, `tests/unit/test_uae_actor.py`, `tests/unit/test_db_mapping.py`, `tests/unit/test_uae_bilingual.py`, `tests/integration/db/test_persistence.py`

**Untracked — new files:**
- `src/lexcrawl/crawling/rotating.py` (pre-existing from the earlier work)
- `tests/unit/test_rotating.py` (pre-existing)
- `AUDIT_REPORT.md` (pre-existing)
- `IMPLEMENTATION_REPORT.md` (this task)

**Deleted:** none. `.env` was not modified.

---

## F. Final verdict

**PARTIALLY READY — BLOCKERS REMAIN.**

All code-level P0/P1 issues are fixed and verified: HTTP responses decode exactly once with typed errors and full regression coverage; Commerce attachment failures are isolated; UAE rotation is reviewed and covered; run errors are classified; conventions are corrected. The **only** remaining blocker is **P0-2**, the unapplied migration `0006`, which was deliberately not applied per the task constraints. API development should not start until the migration is explicitly approved and applied **and** a real `--persist` crawl is verified against the configured database (non-zero rows, idempotent re-run). Unit tests passing is **not** treated as proof of persistence readiness.

