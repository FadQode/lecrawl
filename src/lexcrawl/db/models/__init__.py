"""### ORM models; importing this module registers every table on the metadata."""

from __future__ import annotations

from lexcrawl.db.models.crawl_error import CrawlError
from lexcrawl.db.models.crawl_run import CrawlRun, CrawlRunSource
from lexcrawl.db.models.document import Document
from lexcrawl.db.models.localization import DocumentLocalization
from lexcrawl.db.models.resource import DocumentResource
from lexcrawl.db.models.source import Source

__all__ = [
    "CrawlError",
    "CrawlRun",
    "CrawlRunSource",
    "Document",
    "DocumentLocalization",
    "DocumentResource",
    "Source",
]
