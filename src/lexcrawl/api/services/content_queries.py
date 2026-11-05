"""### Database queries for content (articles and reports)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import and_, func, nullslast, or_, select
from sqlalchemy.orm import Session, selectinload
from sqlalchemy.sql.elements import ColumnElement

from lexcrawl.api.schemas.common import ContentKind
from lexcrawl.db.models import Document, DocumentLocalization, DocumentResource, Source

# $ Type labels win over the source fallback; the two stay consistent with the SQL predicate.$
ARTICLE_TYPES = frozenset({"news_article", "news_release", "news release", "news releases", "press release", "article"})
REPORT_TYPES = frozenset({"report", "reports", "publication", "publication report", "government report"})
SOURCE_KINDS: dict[str, ContentKind] = {
    "uae_legislation_news": "article",
    "cba_news_releases": "article",
    "commerce_reports": "report",
}
ARTICLE_SOURCES = tuple(code for code, kind in SOURCE_KINDS.items() if kind == "article")
REPORT_SOURCES = tuple(code for code, kind in SOURCE_KINDS.items() if kind == "report")


def classify_content(source_code: str, document_type: str | None) -> ContentKind | None:
    """### Classify a stored document as an article or a report."""
    normalized = (document_type or "").strip().lower()
    if normalized in ARTICLE_TYPES:
        return "article"
    if normalized in REPORT_TYPES:
        return "report"
    return SOURCE_KINDS.get(source_code)


def _kind_predicate(kind: ContentKind) -> ColumnElement[bool]:
    """### Build the SQL predicate for one content kind, mirroring `classify_content`."""
    known = list(ARTICLE_TYPES | REPORT_TYPES)
    type_case = func.lower(Document.document_type)
    if kind == "article":
        return or_(
            type_case.in_(list(ARTICLE_TYPES)),
            and_(type_case.notin_(known), Source.code.in_(ARTICLE_SOURCES)),
        )
    return or_(
        type_case.in_(list(REPORT_TYPES)),
        and_(type_case.notin_(known), Source.code.in_(REPORT_SOURCES)),
    )


def _date_conditions(published_from: date | None, published_to: date | None) -> list[ColumnElement[bool]]:
    """### Build publication-date filters, falling back to year for coarse precision."""
    conditions: list[ColumnElement[bool]] = []
    if published_from is not None:
        conditions.append(
            or_(
                Document.published_at >= published_from,
                and_(Document.published_at.is_(None), Document.published_year >= published_from.year),
            )
        )
    if published_to is not None:
        conditions.append(
            or_(
                Document.published_at <= published_to,
                and_(Document.published_at.is_(None), Document.published_year <= published_to.year),
            )
        )
    return conditions


@dataclass(frozen=True, slots=True)
class ContentPage:
    """### One page of content rows with the total match count."""

    rows: list[tuple[Document, Source, DocumentLocalization]]
    total: int


def primary_localization_id() -> Any:
    """### Correlated subquery selecting the lowest localization id for a document."""
    return (
        select(func.min(DocumentLocalization.id))
        .where(DocumentLocalization.document_id == Document.id)
        .correlate(Document)
        .scalar_subquery()
    )


def list_content(
    session: Session,
    kind: ContentKind,
    *,
    source_code: str | None = None,
    published_from: date | None = None,
    published_to: date | None = None,
    limit: int,
    offset: int,
) -> ContentPage:
    """### Return one paginated page of articles or reports with their primary localization."""
    statement = (
        select(Document, Source, DocumentLocalization)
        .join(Source, Document.source_id == Source.id)
        .join(DocumentLocalization, DocumentLocalization.id == primary_localization_id())
        .where(_kind_predicate(kind))
    )
    if source_code is not None:
        statement = statement.where(Source.code == source_code)
    for condition in _date_conditions(published_from, published_to):
        statement = statement.where(condition)
    total = int(session.execute(select(func.count()).select_from(statement.subquery())).scalar_one())
    ordered = (
        statement.order_by(nullslast(Document.published_at.desc()), Document.id.desc()).limit(limit).offset(offset)
    )
    rows = [(document, source, localization) for document, source, localization in session.execute(ordered).all()]
    return ContentPage(rows=rows, total=total)


def get_content(
    session: Session,
    document_id: int,
    kind: ContentKind,
) -> tuple[Document, Source, list[DocumentLocalization], list[DocumentResource]] | None:
    """### Return one content item with its localizations and resources, or None when mismatched."""
    statement = (
        select(Document, Source)
        .join(Source, Document.source_id == Source.id)
        .where(Document.id == document_id)
        .options(selectinload(Document.localizations), selectinload(Document.resources))
    )
    row = session.execute(statement).first()
    if row is None:
        return None
    document, source = row
    if classify_content(source.code, document.document_type) != kind:
        return None
    return document, source, list(document.localizations), list(document.resources)
