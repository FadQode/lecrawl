"""### UAE Legislation news Actor: listing pagination and article extraction."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime
from functools import partial
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from bs4.element import Tag

from lexcrawl.actors.base import Actor, ActorRunResult
from lexcrawl.config import required_setting
from lexcrawl.crawling.base import AccessBlockedError, Fetcher, FetchError
from lexcrawl.logging import get_logger
from lexcrawl.models.document import ArticleContentError, ArticleStub, CanonicalDocument, RawArticle
from lexcrawl.pipeline.normalize import collapse_whitespace, paragraphs_of, text_of
from lexcrawl.validation.uae import (
    PRIMARY_LANGUAGE,
    ExtractionStatus,
    Language,
    UAEArticle,
    UAEArticleLocalization,
    article_key_from_url,
    metadata_document,
    to_canonical,
    translated_article_url,
    validate_article,
    validate_listing_entry,
    validate_localization,
)

LOGGER = get_logger(__name__)

SOURCE_NAME = "uae_legislation_news"
SECONDARY_LANGUAGE: Language = "en"

# $ Selectors observed in the saved listing snapshot for this source.$
CARD_SELECTOR = ".item_"
CARD_LINK_SELECTOR = "a.link_"
CARD_TITLE_SELECTOR = ".title_"
CARD_DATE_SELECTOR = ".dateBox"
CARD_SUMMARY_SELECTOR = ".desc_"
CARD_IMAGE_SELECTOR = ".img_ img"
CARD_IMAGE_SOURCE_SELECTOR = ".img_ source"
PAGINATION_SELECTOR = ".table_pagination"
NEXT_PAGE_CLASS = "next_"

# $ Selectors observed in the saved article snapshot for this source.$
DETAIL_TITLE_SELECTOR = ".section_title h2"
DETAIL_TITLE_FALLBACK_SELECTOR = ".newsDetailBox h2"
DETAIL_CONTAINER_SELECTOR = ".newsDetailBox"
DETAIL_DATE_SELECTOR = ".newsDetailBox .dateBox"
DETAIL_BODY_SELECTOR = ".newsDetailBox .textBox"
DETAIL_IMAGE_SELECTOR = ".newsDetailBox .imgBox img"

_PUBLICATION_DATE_FORMATS = ("%b %d %Y", "%B %d %Y", "%d %b %Y", "%d %B %Y", "%Y-%m-%d")


@dataclass(frozen=True, slots=True)
class ListingPage:
    """### Parsed listing page: article stubs plus the next page link."""

    stubs: tuple[ArticleStub, ...] = ()
    next_page_url: str | None = None
    skipped_cards: int = 0
    duplicate_urls: int = 0


@dataclass(frozen=True, slots=True)
class _LocalizationOutcome:
    """### Result of collecting one language variant of an article."""

    localization: UAEArticleLocalization
    collected_text: bool
    message: str | None = None
    missing_required_fields: bool = False


@dataclass(slots=True)
class _ArticleOutcome:
    """### Assembled bilingual article plus the outcome of each localization."""

    article: UAEArticle | None = None
    localizations: tuple[_LocalizationOutcome, ...] = ()
    issues: tuple[str, ...] = ()


def _soup(html: bytes | str) -> BeautifulSoup:
    """### Parse HTML bytes, letting BeautifulSoup detect the encoding."""
    return BeautifulSoup(html, "lxml")


def _resolve_url(base_url: str, reference: object) -> str | None:
    """### Resolve a possibly relative reference against the page URL."""
    if not isinstance(reference, str):
        return None
    cleaned = reference.strip()
    if not cleaned or cleaned.startswith("#") or cleaned.lower().startswith(("javascript:", "mailto:", "tel:")):
        return None
    return urljoin(base_url, cleaned)


def _has_class(tag: Tag, name: str) -> bool:
    """### Report whether a tag carries the given CSS class token."""
    classes = tag.get("class")
    if isinstance(classes, list):
        return any(str(value) == name for value in classes)
    return classes == name


def article_scope(listing_url: str) -> tuple[str, str]:
    """### Derive the article host and path prefix from the listing URL."""
    parsed = urlparse(listing_url)
    return parsed.netloc.lower(), f"{parsed.path.rstrip('/')}/"


def is_article_url(url: str, *, host: str, path_prefix: str) -> bool:
    """### Report whether a URL is an in-scope article of the configured source."""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False
    candidate = parsed.netloc.lower()
    if candidate != host and not candidate.endswith(f".{host}"):
        return False
    return parsed.path.startswith(path_prefix) and len(parsed.path) > len(path_prefix)


def parse_published_date(value: str) -> date | None:
    """### Parse a source publication date, returning None when unusable."""
    cleaned = collapse_whitespace(value)
    if not cleaned:
        return None
    for date_format in _PUBLICATION_DATE_FORMATS:
        try:
            return datetime.strptime(cleaned, date_format).date()
        except ValueError:
            continue
    return None


def parse_listing(
    html: bytes | str,
    page_url: str,
    *,
    host: str | None = None,
    path_prefix: str | None = None,
) -> ListingPage:
    """### Extract article stubs and the next page link from a listing page."""
    default_host, default_prefix = article_scope(page_url)
    article_host = host if host is not None else default_host
    article_prefix = path_prefix if path_prefix is not None else default_prefix
    soup = _soup(html)
    stubs: list[ArticleStub] = []
    seen: set[str] = set()
    skipped = 0
    duplicates = 0

    for card in soup.select(CARD_SELECTOR):
        link = card.select_one(CARD_LINK_SELECTOR)
        href = _resolve_url(page_url, link.get("href") if isinstance(link, Tag) else None)
        title = text_of(card.select_one(CARD_TITLE_SELECTOR))
        if href is None or not is_article_url(href, host=article_host, path_prefix=article_prefix) or not title:
            skipped += 1
            continue
        if href in seen:
            duplicates += 1
            continue
        seen.add(href)
        stubs.append(
            ArticleStub(
                url=href,
                title=title,
                published_at=parse_published_date(text_of(card.select_one(CARD_DATE_SELECTOR))),
                summary=text_of(card.select_one(CARD_SUMMARY_SELECTOR)) or None,
                image_url=_card_image_url(card, page_url),
            )
        )

    return ListingPage(
        stubs=tuple(stubs),
        next_page_url=_next_page_url(soup, page_url),
        skipped_cards=skipped,
        duplicate_urls=duplicates,
    )


def _card_image_url(card: Tag, page_url: str) -> str | None:
    """### Resolve the listing card image URL from `img` or `source`."""
    image = card.select_one(CARD_IMAGE_SELECTOR)
    if isinstance(image, Tag):
        resolved = _resolve_url(page_url, image.get("src"))
        if resolved is not None:
            return resolved
    source = card.select_one(CARD_IMAGE_SOURCE_SELECTOR)
    if isinstance(source, Tag):
        return _resolve_url(page_url, source.get("srcset"))
    return None


def _next_page_url(soup: BeautifulSoup, page_url: str) -> str | None:
    """### Resolve the pagination 'next' link when the source provides one."""
    container = soup.select_one(PAGINATION_SELECTOR)
    if container is None:
        return None
    for anchor in container.select("a"):
        if not _has_class(anchor, NEXT_PAGE_CLASS):
            continue
        resolved = _resolve_url(page_url, anchor.get("href"))
        if resolved is None or resolved == page_url or not _is_listing_page_url(resolved, page_url):
            return None
        return resolved
    return None


def _is_listing_page_url(candidate_url: str, current_url: str) -> bool:
    """### Keep pagination on the same host and listing path."""
    candidate = urlparse(candidate_url)
    current = urlparse(current_url)
    current_path = current.path.rstrip("/")
    return (
        candidate.scheme in {"http", "https"}
        and candidate.netloc.lower() == current.netloc.lower()
        and (candidate.path.rstrip("/") == current_path or candidate.path.startswith(f"{current_path}/"))
    )


def parse_detail(html: bytes | str, url: str, *, source: str = SOURCE_NAME, language: str = "ar") -> RawArticle:
    """### Extract one article from its detail page, or raise for missing content."""
    soup = _soup(html)
    container = soup.select_one(DETAIL_CONTAINER_SELECTOR)
    if container is None:
        raise ArticleContentError(f"detail container {DETAIL_CONTAINER_SELECTOR!r} not found at {url}")

    title = text_of(soup.select_one(DETAIL_TITLE_SELECTOR)) or text_of(soup.select_one(DETAIL_TITLE_FALLBACK_SELECTOR))
    if not title:
        raise ArticleContentError(f"article title not found at {url}")

    paragraphs = paragraphs_of(soup.select_one(DETAIL_BODY_SELECTOR))
    if not paragraphs:
        raise ArticleContentError(f"article body not found at {url}")

    return RawArticle(
        source=source,
        url=url,
        title=title,
        language=language,
        published_at=parse_published_date(text_of(soup.select_one(DETAIL_DATE_SELECTOR))),
        summary=None,
        image_url=_detail_image_url(soup, url),
        paragraphs=paragraphs,
    )


def _detail_image_url(soup: BeautifulSoup, url: str) -> str | None:
    """### Resolve the article image URL when the source provides one."""
    image = soup.select_one(DETAIL_IMAGE_SELECTOR)
    if isinstance(image, Tag):
        return _resolve_url(url, image.get("src"))
    return None


class UaeLegislationActor(Actor):
    """### Collect UAE news articles with validated Arabic and English localizations."""

    name = SOURCE_NAME

    def __init__(
        self,
        *,
        listing_url: str,
        language: str = "",
        max_listing_pages: int = 25,
        concurrency: int = 1,
        bilingual: bool = False,
        secondary_listing_url: str = "",
        source_name: str = SOURCE_NAME,
    ) -> None:
        """### Configure the listing entry points, bounds, and localization mode."""
        self.listing_url = required_setting(listing_url, name="LISTINGS_URL")
        self.language = language
        self.max_listing_pages = max_listing_pages
        self.concurrency = max(1, concurrency)
        self.bilingual = bilingual
        self.secondary_listing_url = (secondary_listing_url or "").strip()
        self.source_name = source_name
        self.article_host, self.article_path_prefix = article_scope(self.listing_url)
        self._logger = get_logger(f"{__name__}.{source_name}")

    def run(self, fetcher: Fetcher) -> ActorRunResult:
        """### Discover, validate, localize, and assemble this source's articles."""
        started_at = time.monotonic()
        result = ActorRunResult(source=self.source_name)
        discovered: dict[str, dict[str, ArticleStub]] = {}

        complete = self._walk_listing(
            fetcher, discovered, result, listing_url=self.listing_url, language=PRIMARY_LANGUAGE
        )
        if complete and self.bilingual and self.secondary_listing_url:
            secondary = self._walk_listing(
                fetcher,
                discovered,
                result,
                listing_url=self.secondary_listing_url,
                language=SECONDARY_LANGUAGE,
            )
            complete = complete and secondary
        result.pagination_complete = complete

        self._collect_details(fetcher, discovered, result)

        result.duration_seconds = time.monotonic() - started_at
        self._logger.info(
            "actor_run_finished",
            extra={
                "source": self.source_name,
                "status": result.status,
                "documents": len(result.documents),
                "errors": len(result.errors),
                "bilingual": self.bilingual,
                "pagination_complete": result.pagination_complete,
            },
        )
        return result

    def _walk_listing(
        self,
        fetcher: Fetcher,
        discovered: dict[str, dict[str, ArticleStub]],
        result: ActorRunResult,
        *,
        listing_url: str,
        language: Language,
    ) -> bool:
        """### Walk one language's listing, validating records before enrichment."""
        visited: set[str] = set()
        page_url: str | None = listing_url
        # $ Each listing page defines its own article scope, so the secondary language works.$
        page_host, page_prefix = article_scope(listing_url)
        while page_url is not None:
            if page_url in visited:
                result.add_error(f"pagination loop detected at {page_url}")
                return False
            if result.listing_pages_attempted >= self.max_listing_pages:
                result.add_error(f"stopped after hitting max_listing_pages={self.max_listing_pages}")
                return False
            visited.add(page_url)
            result.listing_pages_attempted += 1

            try:
                page = parse_listing(
                    fetcher.fetch(page_url).content,
                    page_url,
                    host=page_host,
                    path_prefix=page_prefix,
                )
            except FetchError as error:
                result.listing_pages_failed += 1
                result.add_error(f"listing fetch failed at {page_url}: {error}")
                return False
            except ArticleContentError as error:
                result.listing_pages_failed += 1
                result.add_error(f"listing parse failed at {page_url}: {error}")
                return False

            result.listing_pages_succeeded += 1
            result.article_urls_deduplicated += page.duplicate_urls
            if page.skipped_cards:
                result.add_error(f"skipped {page.skipped_cards} malformed card(s) on {page_url}")
            for stub in page.stubs:
                problem = validate_listing_entry(
                    title=stub.title,
                    article_url=stub.url,
                    listing_url=page_url,
                    language=language,
                    expected_host=self.article_host,
                )
                if not problem.ok:
                    details = "; ".join(f"{issue.code}: {issue.message}" for issue in problem.errors)
                    result.add_error(f"rejected listing record at {stub.url}: {details}")
                    continue
                key = article_key_from_url(stub.url)
                entry = discovered.setdefault(key, {})
                if language in entry:
                    result.article_urls_deduplicated += 1
                    continue
                entry[language] = stub
            result.articles_discovered = len(discovered)
            page_url = page.next_page_url
        return True

    def _collect_details(
        self,
        fetcher: Fetcher,
        discovered: dict[str, dict[str, ArticleStub]],
        result: ActorRunResult,
    ) -> None:
        """### Localize every discovered article, preserving each language outcome."""
        if not discovered:
            result.add_error("no article URLs discovered on the listing pages")
            return
        outcomes = self._extract_articles(fetcher, list(discovered.items()))
        for outcome in outcomes:
            for localized in outcome.localizations:
                result.detail_pages_attempted += 1
                if localized.collected_text:
                    result.detail_pages_succeeded += 1
                else:
                    result.detail_pages_failed += 1
                    if localized.missing_required_fields:
                        result.records_missing_required_fields += 1
                if localized.message:
                    result.add_error(localized.message)
            for issue in outcome.issues:
                result.add_error(issue)
            if outcome.article is None:
                continue
            document = to_canonical(outcome.article)
            if document is None:
                result.records_without_text += 1
                document = metadata_document(outcome.article)
            result.documents.append(document)

    def _extract_articles(
        self,
        fetcher: Fetcher,
        items: list[tuple[str, dict[str, ArticleStub]]],
    ) -> list[_ArticleOutcome]:
        """### Extract items in order, staying single-threaded when unpooled."""
        if self.concurrency == 1:
            return [self._extract_article(fetcher, key, stubs) for key, stubs in items]
        with ThreadPoolExecutor(max_workers=self.concurrency) as pool:
            return list(pool.map(partial(self._extract_article, fetcher), *(zip(*items, strict=True))))

    def _extract_article(
        self,
        fetcher: Fetcher,
        key: str,
        stubs: dict[str, ArticleStub],
    ) -> _ArticleOutcome:
        """### Collect both localizations of one article and validate the assembly."""
        issues: list[str] = []
        outcomes: list[_LocalizationOutcome] = []
        built: dict[str, UAEArticleLocalization] = {}

        languages: list[Language] = [PRIMARY_LANGUAGE]
        if self.bilingual:
            languages.append(SECONDARY_LANGUAGE)
        for language in languages:
            stub = self._stub_for(key, stubs, language)
            outcome = self._collect_localization(
                fetcher,
                stub=stub,
                language=language,
                listing_url=self._listing_for(language),
            )
            outcomes.append(outcome)
            built[language] = outcome.localization
            if outcome.message:
                issues.append(outcome.message)
            report = validate_localization(outcome.localization, expected_host=self.article_host)
            if not report.ok:
                details = "; ".join(f"{issue.code}: {issue.message}" for issue in report.errors)
                issues.append(f"{stub.url} [{language}] localization rejected: {details}")
                built[language] = outcome.localization.model_copy(
                    update={"extraction_status": "failed", "text": None, "content_hash": None}
                )

        if PRIMARY_LANGUAGE not in built:
            issues.append(f"{key}: primary localization could not be built")
            return _ArticleOutcome(article=None, localizations=tuple(outcomes), issues=tuple(issues))
        try:
            article = UAEArticle(
                article_key=key,
                primary=built[PRIMARY_LANGUAGE],
                translations={
                    language: localization for language, localization in built.items() if language != PRIMARY_LANGUAGE
                },
            )
        except ValueError as error:
            return _ArticleOutcome(
                article=None,
                localizations=tuple(outcomes),
                issues=(*issues, f"{key}: article could not be assembled: {error}"),
            )

        final = validate_article(article, expected_host=self.article_host)
        issues.extend(
            f"{key} {issue.field} {issue.code}: {issue.message}" for issue in (*final.errors, *final.warnings)
        )
        return _ArticleOutcome(article=article, localizations=tuple(outcomes), issues=tuple(issues))

    def _collect_localization(
        self,
        fetcher: Fetcher,
        *,
        stub: ArticleStub,
        language: Language,
        listing_url: str | None,
    ) -> _LocalizationOutcome:
        """### Fetch one language variant and record the actual extraction outcome."""
        try:
            page = fetcher.fetch(stub.url)
            article = parse_detail(page.content, stub.url, source=self.source_name, language=language)
        except AccessBlockedError as error:
            return self._uncollected(
                stub, language, listing_url, "access_blocked", f"article fetch failed at {stub.url}: {error}"
            )
        except FetchError as error:
            status: ExtractionStatus = "unavailable" if getattr(error, "status_code", None) in {404, 410} else "failed"
            return self._uncollected(
                stub, language, listing_url, status, f"article fetch failed at {stub.url}: {error}"
            )
        except ArticleContentError as error:
            return self._uncollected(
                stub,
                language,
                listing_url,
                "failed",
                f"article parse failed at {stub.url}: {error}",
                missing_required_fields=True,
            )

        document = CanonicalDocument.from_raw(article)
        localization = UAEArticleLocalization(
            language=language,
            article_url=stub.url,
            listing_url=listing_url,
            title=article.title,
            published_at=article.published_at or stub.published_at,
            summary=article.summary or stub.summary,
            image_url=article.image_url or stub.image_url,
            text=document.text,
            content_hash=document.content_hash,
            extraction_status="complete",
        )
        return _LocalizationOutcome(localization=localization, collected_text=True)

    def _uncollected(
        self,
        stub: ArticleStub,
        language: Language,
        listing_url: str | None,
        status: ExtractionStatus,
        message: str,
        *,
        missing_required_fields: bool = False,
    ) -> _LocalizationOutcome:
        """### Record a localization whose text could not be collected."""
        localization = UAEArticleLocalization.model_validate(
            {
                "language": language,
                "article_url": stub.url,
                "listing_url": listing_url,
                "title": stub.title,
                "published_at": stub.published_at,
                "summary": stub.summary,
                "image_url": stub.image_url,
                "text": None,
                "content_hash": None,
                "extraction_status": status,
            }
        )
        return _LocalizationOutcome(
            localization=localization,
            collected_text=False,
            message=message,
            missing_required_fields=missing_required_fields,
        )

    def _stub_for(self, key: str, stubs: dict[str, ArticleStub], language: Language) -> ArticleStub:
        """### Return the listing stub for a language, deriving it when absent."""
        if language in stubs:
            return stubs[language]
        source_stub = next(iter(stubs.values()))
        return ArticleStub(
            url=translated_article_url(source_stub.url, target_language=language),
            title=source_stub.title,
            published_at=source_stub.published_at,
            summary=None,
            image_url=source_stub.image_url,
        )

    def _listing_for(self, language: Language) -> str | None:
        """### Return the listing URL that discovered a language variant."""
        if language == PRIMARY_LANGUAGE:
            return self.listing_url
        return self.secondary_listing_url or None
