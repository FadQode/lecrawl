FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

RUN pip install --no-cache-dir uv==0.13.6

COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./

RUN uv sync --frozen --no-dev
ENV PATH="/app/.venv/bin:$PATH"

# $ Run as a non-root user.$
RUN useradd --create-home --uid 10001 app && chown -R app:app /app
# $ Create the state/cache dir owned by app so a mounted volume is writable.$
RUN mkdir -p /app/data && chown app:app /app/data
USER app

EXPOSE 8000
CMD ["uvicorn", "lexcrawl.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
