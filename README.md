# LexCrawl

Crawling and document-ingestion system for public legal, regulatory, and government
information. The working architecture guide lives in `.agents/AGENTS.MD`; agent tooling is
deliberately not committed (see Repository hygiene).

| Source | Kind | Status |
| --- | --- | --- |
| UAE Legislation | news | Implemented and tested; live access is throttled by the site (see Live access status) |
| U.S. Department of Commerce | reports | Implemented, **collecting live records** through the documented public API |
| Canadian Bar Association (CBA) | news releases | Implemented, **35 live releases collected** (complete pagination pass) |

Only the extracted records are ever kept: no HTML page, PDF, or archived snapshot is stored
in this repository or on disk. Records are written as timestamped JSON under `data/` until
the database layer exists.

## Environment

- Interpreter: CPython 3.12 (`.python-version`, `requires-python = ">=3.12,<3.13"`).
- Primary environment: `uv sync` (creates `.venv/` and `uv.lock`).
- `requirements.txt` / `requirements-dev.txt` mirror `pyproject.toml` for environments
  without `uv`; a unit test fails if the two manifests drift apart.
- Browser engine only: the Chromium build must be installed once per machine with
  `uv run playwright install chromium`.
- Configuration comes from environment variables; `.env.example` lists every supported
  name and all are validated at startup by `lexcrawl.config.Settings`.
- `Dockerfile`, `compose.yaml`, and the Alembic scaffold are not created yet.

## Crawling engines

One `Fetcher` contract, two strategies; source actors, parsers, and the orchestrator are
identical for both.

| Engine | When it applies | Notes |
| --- | --- | --- |
| `http` (default) | Server-rendered HTML and JSON APIs | httpx with timeouts, bounded retries, pacing, robots enforcement, no browser cost |
| `browser` | A page source needs a rendered document | Playwright Chromium, same pacing/robots/retry policy, single-threaded by design, one reused page per run |

The browser engine does not spoof a user agent, inject cookies, rotate identities, or use
stealth flags; it runs a plain headless Chromium and identifies itself. Both engines stop on
an access denial and report it as `AccessBlockedError` instead of retrying around it, and
both refuse to retry non-textual payloads such as PDFs or images. API sources are collected
raw, so an API source is skipped with a recorded error when the rendering engine is selected.

## Architecture

Modular monolith with an ingestion worker and (planned) API process. Actors are in-process,
source-specific components behind one shared contract; they emit typed raw records that are
canonicalized before persistence.

```text
src/lexcrawl/
├── actors/
│   ├── base.py                  # Actor contract + ActorRunResult counters
│   ├── uae_legislation.py       # UAE news listing/detail parsing and Actor
│   ├── commerce.py              # Commerce reports Actor over the documented news API
│   └── cba.py                   # CBA news releases Actor over the listing feed + article pages
├── crawling/
│   ├── base.py                  # Fetcher protocol, FetchedPage, fetch errors, block detection
│   ├── http.py                  # httpx strategy: timeouts, retries, block classification
│   ├── browser.py               # Playwright Chromium strategy implementing the same contract
│   ├── pacing.py                # request pacing shared by both strategies
│   └── robots.py                # robots.txt policy shared by both strategies
├── models/document.py           # ArticleStub, RawArticle, CanonicalDocument, TEXT-BEGIN marker
├── validation/
│   ├── base.py                  # shared URL/text/hash validation rules and reports
│   ├── uae.py                   # UAE article/localization contract, validators, canonical adapter
│   └── cba.py                   # CBA release/resource contract, validators, canonical adapter
├── pipeline/normalize.py        # shared whitespace and paragraph extraction
├── pipeline/pdf.py              # PyMuPDF extraction for linked resources
├── orchestrator.py              # runs actors in isolation, aggregates the run report
├── cli.py                       # `lexcrawl crawl` entry point
├── config.py                    # validated settings
└── logging.py                   # structured JSON logging
tests/unit/                      # per-source parser/pipeline tests, fetcher, browser, pacing, robots, CLI tests
tests/integration/               # real-Chromium render feeding the parser, over a local HTTP server
tests/samples.py                 # short structural samples used by parser tests (no archived pages)
data/                            # timestamped extracted records (ignored by git)
migrations/, src/lexcrawl/{db,repositories,scheduler,api}/   # not implemented yet
```

Dependency direction: `models` (pure contracts) is innermost; `crawling` is the external I/O
detail; `actors` depend on `models`, `pipeline`, and the `crawling.base` contract;
`orchestrator` depends on `actors`; `cli` is the composition root that wires settings,
fetcher, and actors. No SQL, HTTP client, or framework object crosses into the contract
layer.

## Running the sources

```bash
uv sync                                                            # install/synchronize dependencies
uv run lexcrawl crawl --source commerce                            # live Commerce report records
uv run lexcrawl crawl --source uae                                  # UAE news over plain HTTP
uv run lexcrawl crawl --source uae --engine browser                 # UAE news with headless Chromium
uv run lexcrawl crawl --source uae --engine browser --headed        # watch the browser work
uv run lexcrawl crawl --source all --max-pages 2                    # every registered source
```

Flags: `--source {uae,commerce,cba,all}`, `--engine {http,browser}`, `--headed`, `--bilingual`, `--max-pages`,
`--request-delay`, `--timeout`, `--max-retries`, `--concurrency`, `--no-robots`, `--out`,
`--log-level`, `--log-format`. The browser engine forces `--concurrency 1` because a
Playwright driver is single-threaded; the runner fails loudly if a browser fetcher is used
from two threads. Exit code is `0` only when every source reports `success`; a blocked,
partial, or empty run exits `1` so schedulers can observe it.

`--out` takes a file path or a directory. A directory (the default, `OUTPUT_DIR=data`)
receives `<UTC timestamp>-<source>.json`; an explicit file is written verbatim. The file is
one JSON array of canonical documents, each of which maps 1:1 to a future database row, with
UTF-8 text (Arabic unescaped).

A stored row always carries the source, canonical URL, title, and language. Its `text` is
either `null` or a collected document body that begins with `TEXT-BEGIN` exactly once, and
`content_hash` is present only when there is text. A row whose body could not be fetched is
still stored with the discovery metadata from the listing (date, summary, image) instead of
being dropped, and the run reports it as `metadata_only`; a later crawl can fill in the text
because records are keyed by canonical URL. Nothing is written when a run collected no
records at all, so an empty file can never be mistaken for collected data.

### UAE Legislation news

- Listing: pages are followed through the site's own `?page=N` links (`.table_pagination`
  next control), bounded by `CRAWL_MAX_LISTING_PAGES`, with repeat-page and loop detection.
  Only same-host URLs under `/ar/news/<slug>` are treated as articles.
- Listing card fields: `a.link_` (URL), `.title_`, `.dateBox`, `.desc_` (optional),
  `.img_ img` with `<source srcset>` fallback. Relative URLs resolve against the page URL.
- Detail fields: `.section_title h2`, `.newsDetailBox .dateBox`,
  `.newsDetailBox .textBox` (paragraphs kept in document order), `.newsDetailBox .imgBox img`.
- Dates: `Jan 08 2026` and near variants parse to `datetime.date`; a genuinely missing date
  stays `null` instead of failing the article.
- Arabic and Unicode text is preserved verbatim; no translation, transliteration, or
  normalization beyond whitespace collapsing.

#### Validation and bilingual localizations

The UAE source validates data in three stages, all inside the normal crawl:

1. **Listing records** are validated before enrichment (title, absolute in-scope URL, URL language
   prefix matching the listing language). Rejected records are reported and never fetched.
2. **Each localization** is validated independently after its own fetch, so a failure in one
   language never discards the other. Statuses are explicit: `complete`, `access_blocked`
   (a denial such as HTTP 403), `failed` (fetch or parse failure), `unavailable` (confirmed
   missing, e.g. HTTP 404), `pending` (not attempted). A 403 is never recorded as `unavailable`.
3. **Content** is checked per language: text must be non-empty, start with `TEXT-BEGIN`, and its
   `content_hash` must equal the shared `compute_content_hash` of that text — the same helper the
   canonical model uses, so there is no second hashing scheme.

Each article is assembled as a `UAEArticle` source contract: a language-independent
`article_key` (the shared slug), an Arabic `primary` localization, and optional `translations`
(English). Each localization keeps its own listing URL, article URL, title, date, summary, image,
text, hash, and status, and metadata differences between languages are preserved and reported as
warnings rather than silently reconciled.

Arabic is the primary representation. The English URL is derived from the verified
language-prefix relationship (`/ar/news/<slug>` ↔ `/en/news/<slug>`), and an optional secondary
listing (`LISTINGS_SECONDARY_URL`) can also be walked so both listings contribute without
duplicating an article. Bilingual collection is off by default (`CRAWL_BILINGUAL=false`,
`--bilingual`); with it off the source behaves exactly as before.

Downstream, `CanonicalDocument` gained a backward-compatible `translations` tuple
(`DocumentTranslation`: language, URL, title, text, hash, date, summary, image). When Arabic
completes, it is the canonical body and English is attached as a translation; if Arabic is
blocked but English completed, the English localization is stored as the document body with its
own URL, hash, and `TEXT-BEGIN` text. Rows whose localizations all failed keep discovery metadata
with `text: null`. Existing sources are unaffected because the field defaults to empty.

### U.S. Department of Commerce reports

Discovery is **budget-aware and incremental**, because Firecrawl credits are limited:

1. **Cheap preflight (no Firecrawl).** The actor fetches the report listing over ordinary HTTP
   through the existing `Fetcher`, parses page 0, and compares the newest identities with the
   persisted `documents.identity_key` set. Outcomes are explicit: `no_change`,
   `new_data`, or `check_failed`.
2. `no_change` finishes the run with **zero Firecrawl calls** and is recorded as a *successful*
   run (`check_outcome=no_change`), not a failure.
3. `check_failed` (403/challenge, missing table, no baseline) also makes **zero** Firecrawl calls,
   records the failure, and never triggers a historical crawl.
4. Only `new_data` starts Firecrawl: listing pages are requested sequentially with explicit
   `?q=<listing-path>&page=N` URLs, paced, bounded by `FIRECRAWL_MAX_PAGES_PER_RUN`, and stop at
   the first page with no new identities (the overlap boundary) or at the source's reported range
   end. Page 0 from the preflight is reused, so only later pages cost credits.
5. Only **new** reports get a detail scrape, bounded by `FIRECRAWL_MAX_DETAIL_SCRAPES_PER_RUN`;
   the remainder is reported as `documents_pending` for a later run. Detail responses are cached
   under `FIRECRAWL_CACHE_DIR`, so a rerun does not pay again for an already acquired page.
6. **Incomplete reports are retried under a persisted, bounded budget.** A failed or
   metadata-only report is never silently treated as done: `documents.extraction_attempts`
   counts each detail attempt (`FIRECRAWL_INCOMPLETE_MAX_ATTEMPTS`, default 3), and
   `incomplete_reports` surfaces those below the budget so a run can retry them directly —
   without a listing pass — capped by `FIRECRAWL_INCOMPLETE_RETRIES_PER_RUN` (default 5).
   Completed reports reset the counter and are excluded. A no-change run with no retry
   candidates still makes **zero** Firecrawl calls; a no-change run with candidates performs
   **only** those detail retries.
7. Discovery is **self-checking**: a provider page that repeats an earlier page's identities
   (pagination not honoured) or a page whose range contradicts its row count is treated as an
   inconsistency that stops the crawl as incomplete, not as an overlap boundary. Any preflight
   identity that discovery cannot confirm is reported as a discrepancy, and
   `articles_discovered` is recorded even when pagination aborts early.
8. Detail pages must contain meaningful body content (`FIRECRAWL_MIN_BODY_WORDS`, default 30).
   Heading-only pages keep their title, date, and attachments but are stored as incomplete
   metadata records instead of failing the run.
9. Attachments go through the **existing** resource pipeline: `_process_resource` →
   `extract_pdf_resource` with `TEXT-BEGIN` and per-resource statuses (`complete`,
   `access_blocked`, `failed`, `url_unavailable`, `not_applicable` for non-PDF). Commerce
   attachments currently answer 403, so PDFs are recorded as `access_blocked` with their original
   URL and metadata retained; no new downloader or Wayback retrieval was added.
10. The listing page is the authoritative target (`REPORTS_LISTING_URL`). The `api.commerce.gov`
    `/api/news` endpoint is **not** used for preflight: the audit found 99/112 known reports but no
    report newer than 2019, so it cannot detect newly published reports.

> **Live-access limitation (measured 2026-11-03).** A plain-`httpx` probe of
> `https://www.commerce.gov/data-and-reports/reports` (and `/robots.txt`) returns **HTTP 403
> with a Cloudflare `Just a moment...` challenge** for this egress, so the cheap preflight
> currently reports `check_failed` and makes **zero** Firecrawl calls. The code fails safe; it
> does not substitute `/api/news`, spoof a browser, or launch a historical crawl. Live Commerce
> ingestion needs an approved access path. This does not affect the Firecrawl listing/detail
> flows, which are exercised by tests through a mocked provider.


Historical acquisition requires the explicit `--bootstrap` flag; an empty database never triggers
a full historical crawl on its own. Budget limits, response cache, and provider credits used are
configurable through the `FIRECRAWL_*` settings in `.env.example`.

### Canadian Bar Association news releases

- **Discovery** uses the site's own listing feed: a `POST` of its `AdvContentRequest` body to
  the endpoint configured in `RELEASES_API_URL` (the site's own listing page makes exactly this
  request; it is CORS-enabled, robots-permitted, and unauthenticated). This is required because
  the visible listing grid is empty in the served HTML and is populated by JavaScript from that
  feed — verified by capturing the page's own network traffic — so plain HTTP cannot page
  through the listing page directly.
- Records are filtered to the article type identified by `RELEASES_TYPE_ID` (taken from the
  live feed and confirmed against known releases and non-releases), then deduplicated by
  canonical URL (`RELEASES_ARTICLE_PATH` + slug, query strings dropped). Filtered-out counts
  are reported.
- **Enrichment** fetches each article page over plain HTTP (server-rendered) and maps:

  | Assignment field | Source |
  | --- | --- |
  | `title` | `h1.printTitle` |
  | `source_url` | canonical article URL (`RELEASES_SITE_URL` + `RELEASES_ARTICLE_PATH` + slug) |
  | `published_at` | `.articleDateLogo`, falling back to the listing record's dates |
  | `type_of_law` | `.newBadge` badge (for example `News Releases`), falling back to the listing type |
  | `topics` | `section.tags a`, verbatim and in page order (for example `#Mortgages`), never inferred |
  | `summary` | `div.inline-summary` (Summary Points) |
  | `text` | `#section-to-print`: every heading and paragraph in reading order |
  | `additional_information` | `div.sticky-sidebar` Additional information links, with title, URL, and format |

- Summary Points, the topic list, and the resource sidebar are rendered separately, so they are
  removed from the body text to avoid duplication. Nothing is truncated at the fold.
- **Linked resources**: each Additional information link is stored with its title, URL, and
  format (`pdf`, `excel`, and so on). PDFs are downloaded (§ binary fetch), their text is
  extracted with PyMuPDF and prefixed with `TEXT-BEGIN`, and the binary is discarded — only the
  original URL plus the extracted text are stored. Excel and other formats are kept as metadata
  only. A failed download or extraction is recorded as an explicit resource error and never as
  empty text.
- Article pages that cannot be fetched or parsed still produce a stored row with the discovery
  metadata (`text: null`, `type_of_law: News Releases`), counted as `metadata_only`.

#### Release contract and resource validation

The CBA source has an explicit contract (`validation/cba.py`), validated inside the crawl:

- `CbaNewsRelease`: fixed `source`, `source_url`, non-empty `title`, fixed
  `document_type` ("News Releases"), optional `native_id`, `published_at`, `topics`, `summary`,
  `text`, `content_hash`, `image_url`, and `resources`.
- `CbaResource`: `title`, optional `original_url`, `resource_type` (`pdf`, `excel`, `word`, `csv`,
  `other`), `mime_type`, `file_size`, `extracted_text`, `content_hash`, `extraction_status`
  (`pending`, `complete`, `not_applicable`, `access_blocked`, `failed`, `url_unavailable`), plus a
  generated `display_title` and `error`.

Rules: source/document type must match, the URL must be absolute and in scope, topics are
normalized (stripped, de-duplicated, order kept), and the article body is checked with the shared
content rules — non-empty when present, `TEXT-BEGIN` prefix, and a hash equal to
`compute_content_hash(text)`. A release without body text is a valid *incomplete* record, never a
failed one.

Each resource is validated independently, so a bad attachment never invalidates the release or
its siblings: a PDF marked `complete` needs its own marked text and hash; Excel and other
attachments are `not_applicable` and need no extraction; a resource whose URL cannot be resolved
keeps its filename metadata and is marked `url_unavailable` (no URL is ever invented).

Multiple PDFs are processed one by one, each with its own URL, text, and hash, and each gets a
`display_title` of the form `Title [file.pdf]` so the resulting documents are distinguishable
while the original title is preserved.

The article body and the PDF texts stay separate: attached resources are nested under the parent
document (`CanonicalDocument.resources`), which keeps the relationship intact and never mixes PDF
text into the article text. The canonical model's `DocumentResource` gained optional
`display_title`, `mime_type`, `file_size`, `content_hash`, and `status` fields (all defaulted, so
UAE and Commerce are unaffected).

#### Report contract, dates, and resources

Commerce has an explicit contract (`validation/commerce.py`) validated inside the crawl:

- `CommerceReport`: fixed `source`, `source_url`, non-empty `title`, `published_month`,
  `published_year`, `published_date_raw`, `published_precision`, optional `text`, `content_hash`,
  `image_url`, `native_id`, `api_created_at`, `resources`, and provenance
  (`provenance`, `archive_url`, `archived_at`, `archive_listing_url`).
- `CommerceResource`: `title`, optional `original_url`, `resource_type`, `mime_type`,
  `file_size`, `extracted_text`, `content_hash`, `extraction_status`, plus `display_title`,
  `error`, `archive_url`, `archived_at`.

**Publication dates are month-precision.** The listing shows values such as `January 2025`; the
parser (`parse_listing_date`) extracts month and year, preserves the original string, and **never
invents a day**. The canonical document keeps `published_at` for sources with exact dates and, for
Commerce, sets `published_at: null` with `published_month`/`published_year`/`published_date_raw`
and `published_precision: "month"`. An absent date is a warning, never a guess — the API's
`created` timestamp is retained only as `api_created_at` provenance and is **not** used as a
publication date. Attached resources inherit their period through the parent report.

**Resources stay separate.** One report owns zero or more resources: a single PDF, several PDFs
(each with its own URL, text, hash, and a `display_title` of `Title [file.pdf]`), or no PDF at all
(in which case the report page text fills the report `text`). Attachments without a recoverable
URL keep their filename/MIME/size metadata and are marked `url_unavailable` — a URL is never
fabricated from a filename. Excel and other types are metadata only and never go through PDF
extraction. A failed attachment never invalidates the report: resource findings are reported
independently.

**API vs archive representations are distinct.** `from_api_payload` builds a `live` report from
the API (body text, lowercase `image`, attachment metadata without URLs, no verified publication
date) while `from_listing_row` builds a `wayback` report from an archived listing row (month-year
date, page URL, PDF URLs, capture timestamps as provenance only). Both map to the same canonical
contract, so they can be reconciled later without merging here. API pagination uses explicit
`page[offset]` values on the configured host; a foreign-host `links.next` is ignored.

### Courtesy and reliability

Explicit connect/read timeouts, bounded retries with exponential backoff for transient
statuses (408/425/429/5xx) and transport errors, no retry for other 4xx, a configurable delay
between request starts, bounded concurrency for detail pages, per-run URL caching, and
`robots.txt` enforcement (`CRAWL_RESPECT_ROBOTS_TXT`). A robots file that is missing or
unreachable is treated as allow-all and logged as a warning.

### Live access status

**Release feed source (working, complete pass).** On 2026-10-30 `uv run lexcrawl crawl
--source cba` traversed the listing feed to exhaustion (`CBA_PAGE_SIZE=200`, 2 feed pages, 281
records scanned), filtered 246 non-release records, discovered **35 news releases**, enriched
**35/35** article pages, and exited `0` with `status=success`. Every stored row carries
`type_of_law: News Releases`, at least one verbatim topic (for example `#Mortgages`, `#Fraud`,
`#Financial Crime`), a `TEXT-BEGIN` body (median 3,323 characters, max 6,267), and an image.
One release exposes the Additional information block from the assignment example: its PDF text
was extracted and stored against the original PDF URL while the Excel link was retained as
metadata. All 35 rows share unique canonical URLs.

**Reports API source (working).** On 2026-10-29 the pipeline collected **41 real
report records** from the first API page: every record had a title, canonical URL, language,
publication date, `TEXT-BEGIN` body text, and content hash, and all 41 rows were written to
JSON. The run reported `status=partial_failure` because page 2 returned a Cloudflare
challenge (`Just a moment...`), which was recorded as an access denial rather than retried
around; the already-collected records were preserved, which is the intended partial-failure
behaviour. The API host publishes no `robots.txt` (HTTP 404), so the allow-all fallback
applies and is logged.

**Primary news source (throttled).** The configured listing URL (`LISTINGS_URL`) answers
**HTTP 403** with a Cloudflare bot-protection page (*"Sorry, you have been blocked …"*,
`server: cloudflare`, Ray ID) for the plain HTTP engine from this egress, and the same denial
appears with headless Chromium on any path once the site's small per-session budget is spent.
Static paths (`/robots.txt`, `/favicon.ico`) still answer 200. Measured behaviour on 2026-10-29 with one browser session and no identity rotation:

- Listing page: **HTTP 200, real content, 20 live articles discovered** (`?page=2` present).
- Listing page: **HTTP 200, real content**, and all **20 cards parse** (title + date + URL for
  20/20, summary and image for 19/20). That metadata alone is stored for every article.
- Detail pages: the first **2** paced requests returned 200; every request after that was
  denied. Repeating the run reproduced the same small budget.
- Result of the latest run: `status=partial_failure`, exit code 1, **20 rows stored** — 1 with
  `TEXT-BEGIN` body text and 19 as metadata-only rows carrying the Arabic title, date,
  canonical URL, summary, and image. Nothing was fabricated, no blocked page was treated as
  an article, and the blocked fetches are listed as actionable errors. Because rows are keyed
  by canonical URL, repeated daily runs accumulate the bodies the site does serve.

The UAE denial is enforced per session/egress IP, so it cannot be fixed from client code, and
this project does not attempt to defeat it (no cookie injection, session or proxy rotation,
stealth flags, or challenge solving). Collecting that source fully needs an approved access
path: run from a network the site allows, or ask the site operator to permit the crawler's
user agent.

## Persistence

PostgreSQL storage for the canonical ingestion output is available under `src/lexcrawl/db/`,
`src/lexcrawl/repositories/`, and `src/lexcrawl/persistence.py`, with Alembic migrations in
`migrations/`. Persistence is opt-in (`--persist`) and never changes the JSON output. Full schema,
identity/idempotency rules, the canonical→database mapping, and operational commands are documented
in [`docs/database.md`](docs/database.md).

```bash
docker build -t lexcrawl-postgres:16 docker/postgres
docker run -d --name lexcrawl-postgres -p 5432:5432 lexcrawl-postgres:16
uv run lexcrawl db upgrade && uv run lexcrawl db seed
uv run lexcrawl crawl --source uae --persist
```

## Development

```bash
uv run pytest                                                    # network-free suite
uv run coverage run -m pytest && uv run coverage report           # coverage
uv run ruff check . && uv run ruff format --check .              # lint and format
uv run mypy                                                      # strict types over src/
```

Tests never touch the live sites and never write into the repository: parsing uses the short
structural samples in `tests/samples.py`, HTTP behavior uses `respx` mocks with a synthetic
clock, orchestration uses an offline payload server, and the marked `integration` tests drive
a local `http.server` with a real headless Chromium (they skip automatically when no browser
binary is present).

## Repository hygiene

Only deliverables are tracked. Local tooling and generated state are ignored: `.venv/`,
caches (`__pycache__`, `.pytest_cache`, `.mypy_cache`, `.ruff_cache`, `.coverage`), local
`.env` files, captured secrets (`*cookies*.json`, `storage-state*.json`), extracted records
under `data/`, and all agent tooling (`.agents/`, `.kilo/`, `.claude/`, `.grok/`,
`skills-lock.json`). No archived page or PDF is stored anywhere in the project.

## Conventions from the assignment

- Every function has a short docstring starting with `###`.
- Every code comment ends with `$`.
- Every collected text document starts with `TEXT-BEGIN`.
- PDFs are never retained; only the original URL plus extracted text are stored. PDF text
  extraction is implemented (`pipeline/pdf.py`, PyMuPDF) and exercised live by the CBA source.
  The Commerce API's `documents` entries still carry no usable file URL, so that source has no
  PDF to extract.
