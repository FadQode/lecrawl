# syntax=docker/dockerfile:1

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# $ Install the project (deps + console script), then the Chromium needed by the UAE browser engine.$
COPY pyproject.toml requirements.txt README.md ./
COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./
RUN pip install --upgrade pip \
    && pip install . \
    && playwright install --with-deps chromium

EXPOSE 8000

# $ Apply migrations and seed the source registry before serving; a single worker is required.$
CMD ["sh", "-c", "lexcrawl db upgrade && lexcrawl db seed && exec uvicorn lexcrawl.api:create_app --factory --host 0.0.0.0 --port 8000"]
