# LexCrawl

Crawling and document-ingestion system for public legal, regulatory, and government
information. See `.agents/AGENTS.MD` for the authoritative guide; this file records the
declared environment and folder architecture only.

## Sources

| Source | Kind |
| --- | --- |
| UAE Legislation | news |
| Canadian Bar Association (CBA) | news releases |
| U.S. Department of Commerce | reports |

## Environment (uv)

- Interpreter: CPython 3.12 (pinned in `.python-version`, `requires-python = ">=3.12,<3.13"`).
- Environment and lockfile: `uv sync` (creates `.venv/` and `uv.lock`).
- Optional browser crawling: `uv sync --extra browser`.
- Configuration is read from environment variables; see `.env.example` for the full list.
- Dev tooling (pytest, ruff, mypy, respx) lives in the `dev` dependency group.

## Architecture

Modular monolith: one codebase, two processes (`api`, `worker`), PostgreSQL, Alembic
migrations. Actors are in-process, source-specific components with a shared contract;
they emit typed raw records to a shared pipeline that normalizes, validates, processes
PDFs, and persists through repositories.

```text
src/lexcrawl/
├── actors/          # base.py + one module per source (uae_legislation, cba, commerce)
├── crawling/        # http.py (primary), browser.py (optional Playwright)
├── pipeline/        # normalize.py, validate.py, pdf.py
├── models/          # document.py - typed data contract / canonical document
├── db/              # models.py (SQLAlchemy), session.py (engine/session factory)
├── repositories/    # documents.py, crawl_runs.py - all SQL lives here
├── scheduler/       # daily.py - daily run trigger + run coordination
├── api/             # main.py + routes/{documents,sources,jobs}.py
├── config.py        # centralized, validated settings
└── logging.py       # structured logging
tests/               # unit/, integration/, api/
migrations/          # Alembic
fixtures/            # saved source HTML/PDF samples for parsing tests
```

### Declared but not yet created

`Dockerfile`, `compose.yaml` (`api`, `worker`, `db`), `AGENT.md`, Alembic config, and all
`*.py` modules. Folders exist with empty `__init__.py` markers only; no implementation
has been written.

## Conventions from the assignment

- Every function has a short docstring starting with `###`.
- Every code comment ends with `$`.
- Every collected text document starts with `TEXT-BEGIN`.
- PDFs are never retained; only the original URL plus extracted text are stored.
