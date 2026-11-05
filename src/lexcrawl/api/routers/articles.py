"""### Article endpoints: news articles and news releases."""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query

from lexcrawl.api.dependencies import PageParamsDep, SessionDep
from lexcrawl.api.exceptions import NotFoundError
from lexcrawl.api.openapi import READ_BY_ID, VALIDATION_ONLY
from lexcrawl.api.schemas.articles import ArticleDetail, ArticleSummary
from lexcrawl.api.schemas.common import Page, Pagination
from lexcrawl.api.schemas.resources import ResourceOut, ResourceParent
from lexcrawl.api.services import content_queries

router = APIRouter(tags=["Articles"])


@router.get(
    "/articles",
    response_model=Page[ArticleSummary],
    summary="List articles",
    description=(
        "Return a paginated list of articles (news articles and news releases). "
        "Ordered by publication date descending (NULLs last), then id descending. "
        "Extracted bodies are not included; use the detail endpoint for content."
    ),
    responses=VALIDATION_ONLY,
)
def list_articles(
    session: SessionDep,
    page: PageParamsDep,
    source: Annotated[str | None, Query(description="Filter by source code, e.g. cba_news_releases")] = None,
    published_from: Annotated[
        date | None, Query(description="Earliest publication date (inclusive, YYYY-MM-DD)")
    ] = None,
    published_to: Annotated[date | None, Query(description="Latest publication date (inclusive, YYYY-MM-DD)")] = None,
) -> Page[ArticleSummary]:
    """### Return a paginated, filterable list of articles."""
    result = content_queries.list_content(
        session,
        "article",
        source_code=source,
        published_from=published_from,
        published_to=published_to,
        limit=page.limit,
        offset=page.offset,
    )
    items = [
        ArticleSummary.from_row(document, source_model, localization)
        for document, source_model, localization in result.rows
    ]
    return Page(items=items, pagination=Pagination(limit=page.limit, offset=page.offset, total=result.total))


@router.get(
    "/articles/{article_id}",
    response_model=ArticleDetail,
    summary="Get an article",
    description="Return one article with its marked extracted body, localizations, and resources.",
    responses=READ_BY_ID,
)
def get_article(article_id: int, session: SessionDep) -> ArticleDetail:
    """### Return one article with its full extracted content and metadata."""
    found = content_queries.get_content(session, article_id, "article")
    if found is None:
        raise NotFoundError(f"article {article_id} was not found")
    document, source, localizations, resources = found
    return ArticleDetail.from_rows(document, source, localizations, resources)


@router.get(
    "/articles/{article_id}/resources",
    response_model=list[ResourceOut],
    summary="List article resources",
    description="Return the linked resources (attachments) belonging to one article.",
    responses=READ_BY_ID,
)
def list_article_resources(article_id: int, session: SessionDep) -> list[ResourceOut]:
    """### Return the resources attached to one article."""
    found = content_queries.get_content(session, article_id, "article")
    if found is None:
        raise NotFoundError(f"article {article_id} was not found")
    document, source, localizations, resources = found
    primary_title = sorted(localizations, key=lambda item: item.id)[0].title if localizations else None
    parent = ResourceParent.from_models(document, source, "article", primary_title)
    return [ResourceOut.from_model(resource, parent=parent) for resource in sorted(resources, key=lambda item: item.id)]
