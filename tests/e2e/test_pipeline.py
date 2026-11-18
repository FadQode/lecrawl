"""### End-to-end test: source Actor -> content processing -> PostgreSQL."""

from __future__ import annotations

import pytest
from sqlalchemy import delete, select

from lexcrawl.actors.uae_legislation import UaeLegislationActor
from lexcrawl.crawling.scrape import ScrapedPage
from lexcrawl.db.models import Document, Source
from lexcrawl.db.session import session_scope
from lexcrawl.repositories.sources import ensure_source, source_id_for
from lexcrawl.services.content_processing import ContentProcessingService
from samples import UAE_LISTING_URL, uae_detail_html, uae_listing_html

TEST_SOURCE = "test_e2e_source"
DETAIL_1 = f"{UAE_LISTING_URL}/article-1"


class FakeScraper:
    """### Returns canned HTML for known URLs."""

    def __init__(self, pages: dict[str, str]) -> None:
        """### Store canned pages keyed by URL."""
        self._pages = pages

    def scrape(self, url: str, *, cache: bool = False) -> ScrapedPage:
        """### Return the canned page."""
        return ScrapedPage(
            requested_url=url,
            provider_status=200,
            target_status=200,
            html=self._pages[url],
            markdown="",
            credits_used=0,
        )

    def store(self, url: str, page: ScrapedPage) -> None:
        """### No-op cache store."""
        return None

    def evict(self, url: str) -> None:
        """### No-op cache eviction."""
        return None

    def close(self) -> None:
        """### No-op close."""
        return None


@pytest.mark.db
def test_actor_to_content_processing_to_postgres(pg_factory) -> None:  # type: ignore[no-untyped-def]
    """### The UAE Actor's output is persisted idempotently as canonical documents."""
    with session_scope(pg_factory) as session:
        session.execute(delete(Source).where(Source.code == TEST_SOURCE))
    with session_scope(pg_factory) as session:
        ensure_source(session, TEST_SOURCE, "Test e2e source")

    scraper = FakeScraper(
        {
            UAE_LISTING_URL: uae_listing_html(cards=1, next_page=None).decode(),
            DETAIL_1: uae_detail_html().decode(),
        }
    )
    actor = UaeLegislationActor(
        listing_url=UAE_LISTING_URL,
        firecrawl=scraper,
        max_pages=2,
        max_detail_scrapes=5,
        min_body_words=3,
        source_name=TEST_SOURCE,
    )
    try:
        result = actor.run(None)
        assert result.documents, "actor produced no documents"

        service = ContentProcessingService(pg_factory)
        first = service.persist(TEST_SOURCE, result.documents)
        assert first.documents == len(result.documents)
        assert first.inserted == len(result.documents)  # first pass inserts all

        with session_scope(pg_factory) as session:
            source_id = source_id_for(session, TEST_SOURCE)
            assert source_id is not None
            rows = session.scalars(select(Document).where(Document.source_id == source_id)).all()
            assert len(rows) == len(result.documents)
            assert all(row.extraction_status == "complete" for row in rows)
            assert all(row.text is not None and row.text.startswith("TEXT-BEGIN") for row in rows)

        # $ Reprocessing creates no duplicates (identity-keyed upsert).$
        second = service.persist(TEST_SOURCE, result.documents)
        assert second.inserted == 0 and second.updated == len(result.documents)
        with session_scope(pg_factory) as session:
            source_id = source_id_for(session, TEST_SOURCE)
            assert source_id is not None
            remaining = session.scalars(select(Document).where(Document.source_id == source_id)).all()
            assert len(remaining) == len(result.documents)
    finally:
        with session_scope(pg_factory) as session:
            source_id = source_id_for(session, TEST_SOURCE)
            if source_id is not None:
                session.execute(delete(Document).where(Document.source_id == source_id))
            session.execute(delete(Source).where(Source.code == TEST_SOURCE))
