# LexCrawl

Crawling and document-ingestion system for public legal, regulatory, and government
information. The working architecture guide lives in `.agents/AGENTS.MD`.

This repository is being rebuilt **thin, one source at a time**. Only the Canadian Bar
Association (CBA) news-releases source is implemented so far; the UAE and Commerce sources,
persistence, the API, and scheduling are planned next.

## Status

| Layer | State |
| --- | --- |
| CBA news releases (fetch → parse → canonical JSON) | Implemented |
| UAE Legislation / Commerce | Not started |
| PDF attachment extraction | Not started |
| PostgreSQL persistence | Not started |
| FastAPI + Swagger UI | Not started |
| Daily scheduler + Docker Compose | Not started |

## Layout

```text
src/lexcrawl/
├── actors/
│   ├── base.py        # Actor contract + ActorRunResult counters
│   └── cba.py         # CBA listing feed + article page parsing and Actor
├── crawling/
│   ├── base.py        # Fetcher protocol, FetchedPage, fetch errors, block detection
│   └── http.py        # httpx strategy: timeouts, bounded retries, pacing, redirect checks
├── models/document.py # RawArticle, CanonicalDocument, TEXT-BEGIN marker, hashing
├── pipeline/normalize.py  # shared whitespace and paragraph extraction
├── orchestrator.py    # runs actors in isolation, aggregates the run report
├── cli.py             # `lexcrawl crawl` entry point
├── config.py          # validated settings
└── logging.py         # structured JSON logging
tests/unit/            # parser, fetcher, contract, CLI tests (mocked network)
tests/samples.py       # short structural fixtures (no archived pages)
```

Dependency direction: `models` (pure contracts) is innermost; `crawling` is the external I/O
detail; `actors` depend on `models`, `pipeline`, and the `crawling.base` contract; `orchestrator`
depends on `actors`; `cli` is the composition root.

## Quick start

```bash
uv sync
cp .env.example .env          # set CRAWL_USER_AGENT if you like
uv run lexcrawl crawl --source cba
uv run lexcrawl crawl --source cba --max-pages 1 --out data/cba.json
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

## Conventions

Per the technical assignment: every function docstring begins with `###`, every code comment
ends with `$`, and every collected text document starts with `TEXT-BEGIN`.
