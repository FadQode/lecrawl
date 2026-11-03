# $ Development and test database image for LexCrawl. It is a local fixture, not a deployment artefact.$
FROM postgres:16-alpine

# $ Defaults match .env.example so a bare container is immediately usable.$
ENV POSTGRES_DB=lexcrawl \
    POSTGRES_USER=lexcrawl \
    POSTGRES_PASSWORD=lexcrawl

# $ Fail fast when the server cannot accept connections, so tests never race startup.$
HEALTHCHECK --interval=5s --timeout=3s --start-period=10s --retries=12 \
    CMD pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"

EXPOSE 5432
