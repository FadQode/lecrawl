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

Flags: `--source {uae,commerce,all}`, `--engine {http,browser}`, `--headed`, `--max-pages`,
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

### U.S. Department of Commerce reports

- Collection uses the documented public API configured in `REPORTS_API_URL`, so there is no
  HTML scraping for this source. The API answers with a
  JSON:API envelope (`data` records plus `links.next`), and pagination follows that next link
  with the API key re-attached, bounded by `COMMERCE_MAX_PAGES`.
- Records are filtered client-side by `COMMERCE_NEWS_TYPES` (default `Reports`) and
  deduplicated by canonical URL; the count of dropped records is reported as `filtered`.
- In-scope URLs are the paths listed in `REPORTS_ARTICLE_PATHS` on the host set by
  `REPORTS_ARTICLE_HOST`; records outside them are skipped and counted.
- Fields: `label` → title, `href`/`self` → canonical URL, `body` → paragraph text,
  `teaser`/`subtitle` → summary, `news_type[].label` → the filter. Publication date prefers
  `post_date`, then `post_date_formatted`, then `created`/`updated` (unix seconds); records
  without any usable date are counted and reported, not dropped.
- The default `COMMERCE_API_KEY=DEMO_KEY` is api.data.gov's public demo key, limited to
  about 30 requests per hour and 50 per day per IP. Request a free key and set
  `COMMERCE_API_KEY` for regular collection.

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
