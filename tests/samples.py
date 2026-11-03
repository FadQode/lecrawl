"""### Minimal structural samples for parser tests.

This repository stores no archived pages and names no live site: these builders emit short
documents that mirror the markup contracts verified against the live sources, using reserved
example hosts and synthetic text. They are the only markup the test suite parses.
"""

from __future__ import annotations

import json
from collections.abc import Sequence

LISTING_HOST = "listing.example.test"
LISTING_URL = f"https://{LISTING_HOST}/ar/news"
ARTICLE_PREFIX = f"{LISTING_URL}/"
PAGE_TWO_URL = f"{LISTING_URL}?page=2"

SAMPLE_TITLE = "إعلان تجريبي: تفاصيل الخبر الأول عن المنظومة الجديدة"
ARTICLE_PARAGRAPHS: tuple[str, ...] = (
    "أعلنت الجهة المعنية عن تفاصيل الخبر الأول، مشيرة إلى أن المنظومة الجديدة تدخل حيز التنفيذ "
    "بالتعاون مع الشركاء المعنيين في القطاع.",
    "وأوضحت أن المرحلة التالية تشمل تبادل الخبرات ومراجعة النصوص ذات الصلة بما يضمن وضوح الإجراءات واستمراريتها.",
)
ARTICLE_DATE = "Jan 08 2026"
ARTICLE_IMAGE = "https://listing.example.test/storage/post/large/sample.png"

EN_LISTING_URL = f"https://{LISTING_HOST}/en/news"
EN_ARTICLE_PREFIX = f"{EN_LISTING_URL}/"
EN_PAGE_TWO_URL = f"{EN_LISTING_URL}?page=2"
EN_TITLE = "Sample English release title for the listings"
EN_PARAGRAPHS: tuple[str, ...] = (
    "The authority announced the details of the first release, noting that the new framework takes effect "
    "together with the relevant partners in the sector.",
    "It added that the next phase covers sharing expertise and reviewing the related texts so that "
    "procedures stay clear and continuous.",
)

REPORTS_HOST = "reports.example.test"
REPORTS_API_URL = "https://feed.example.test/content-api"
REPORTS_ARTICLE_PATHS = ("/news/", "/reports/")
REPORTS_ARTICLE_URL = f"https://{REPORTS_HOST}/reports/2026/01/annual-report"

RELEASES_API_URL = "https://feed.example.test/content-api"
RELEASES_SITE_URL = "https://releases.example.test"
RELEASES_ARTICLE_PATH = "/article/"
RELEASES_TYPE_ID = "00000000-0000-4000-8000-000000000001"
RELEASES_OTHER_TYPE_ID = "00000000-0000-4000-8000-000000000002"
RELEASES_PDF_URL = "/Assets/documents/statistics/sample-data.pdf"
RELEASES_EXCEL_URL = "/Assets/documents/statistics/sample-data.xlsx"
RELEASES_SECTIONS = (
    ("First section heading", "First section paragraph."),
    ("Second section heading", "Second section paragraph."),
    ("Third section heading", "Third section paragraph."),
)

COMMERCE_LISTING_URL = "https://www.commerce.gov/reports"
COMMERCE_LISTING_CAPTURE = "20261015120000"
COMMERCE_PDF_URLS = (
    "https://www.commerce.gov/sites/default/files/2025-01/annual-report.pdf",
    "https://www.commerce.gov/sites/default/files/2025-01/annual-report-appendix.pdf",
)


def listing_html(
    *,
    cards: int = 20,
    next_page: str | None = PAGE_TWO_URL,
    duplicate_first: bool = False,
    language: str = "ar",
) -> bytes:
    """### Build a UAE listing document using the observed card markup."""
    prefix = "/ar/news/" if language == "ar" else "/en/news/"
    label = "خبر رقم" if language == "ar" else "Release number"
    summary_label = "ملخص الخبر رقم" if language == "ar" else "Summary of release"
    items: list[str] = []
    for index in range(1, cards + 1):
        summary = "" if index == 1 else f'<div class="desc_">{summary_label} {index}</div>'
        items.append(
            '<div class="item_ animated_wrapper">'
            f'<a class="link_" href="{prefix}article-{index}">'
            f'<div class="image_box"><div class="img_"><img src="/storage/post/large/{index}.png"/></div></div>'
            '<div class="content_">'
            f'<div class="dateBox"><span>Jan {index:02d} 2026</span></div>'
            f'<div class="title_">{label} {index}</div>'
            f"{summary}"
            "</div></a></div>"
        )
    if duplicate_first and items:
        items.append(items[0])
    pagination = ""
    if next_page:
        pagination = (
            '<div class="table_pagination"><ul>'
            f'<li><a class="page-link l_ next_" href="{next_page}">next</a></li>'
            "</ul></div>"
        )
    document = (
        '<!DOCTYPE html><html lang="ar" dir="rtl"><head><meta charset="utf-8">'
        "<title>news</title></head><body><main>"
        f"{''.join(items)}{pagination}"
        "</main></body></html>"
    )
    return document.encode()


def detail_html(
    *,
    title: str = SAMPLE_TITLE,
    paragraphs: Sequence[str] = ARTICLE_PARAGRAPHS,
    date_text: str | None = ARTICLE_DATE,
    image_url: str | None = ARTICLE_IMAGE,
    with_container: bool = True,
    with_title_block: bool = True,
) -> bytes:
    """### Build a UAE article document using the observed detail markup."""
    title_block = f'<div class="section_title"><h2>{title}</h2></div>' if with_title_block else ""
    date_block = f'<div class="dateBox"><span>{date_text}</span></div>' if date_text else ""
    image_block = f'<div class="imgBox"><img src="{image_url}"/></div>' if image_url else ""
    body = "".join(f"<p>{paragraph}</p>" for paragraph in paragraphs)
    container = (
        f'<div class="newsDetailBox">{date_block}{image_block}<div class="textBox">{body}</div></div>'
        if with_container
        else ""
    )
    document = (
        '<!DOCTYPE html><html lang="ar" dir="rtl"><head><meta charset="utf-8">'
        f"<title>article</title></head><body><main>{title_block}{container}</main></body></html>"
    )
    return document.encode()


def commerce_record(
    *,
    record_id: str = "1",
    label: str = "Department of Commerce publishes an annual report",
    href: str = REPORTS_ARTICLE_URL,
    body: str | None = "<p>First report paragraph.</p><p>Second report paragraph.</p>",
    teaser: str | None = "A short teaser.",
    subtitle: str | None = None,
    news_types: Sequence[str] = ("Reports",),
    post_date: object = "2026-01-15",
    post_date_formatted: object = None,
    created: object = 1768435200,
    documents: Sequence[dict[str, object]] | None = None,
    image: object = None,
) -> dict[str, object]:
    """### Build one reports API record with the verified field names."""
    return {
        "id": record_id,
        "label": label,
        "href": href,
        "self": href,
        "body": body,
        "teaser": teaser,
        "subtitle": subtitle,
        "news_type": [{"id": str(index), "label": label} for index, label in enumerate(news_types, start=1)],
        "post_date": post_date,
        "post_date_formatted": post_date_formatted,
        "created": created,
        "updated": created,
        "documents": list(documents or []),
        "image": image,
    }


def commerce_page(records: Sequence[dict[str, object]], *, next_page: str | None = None) -> bytes:
    """### Build a JSON:API response envelope for the reports API."""
    document: dict[str, object] = {
        "jsonapi": {"version": "1.1"},
        "data": list(records),
        "links": {},
    }
    if next_page:
        document["links"] = {"next": {"href": next_page}}
    return json.dumps(document, ensure_ascii=False).encode()


def cba_listing_record(
    *,
    slug: str = "sample-release",
    title: str = "Sample release title for the listings",
    article_date: object = "2026-09-25T00:00:00",
    posted_date: object = "2026-09-20T00:00:00",
    summary: str | None = "",
    type_ids: Sequence[str] = (RELEASES_TYPE_ID,),
    with_image: bool = True,
) -> dict[str, object]:
    """### Build one releases listing record with the verified API field names."""
    return {
        "SEOName": slug,
        "Title": title,
        "ArticleDate": article_date,
        "PostedDate": posted_date,
        "Summary": summary,
        "ArticleTypes": list(type_ids),
        "ArticleTopics": ["00000000-0000-4000-8000-0000000000ff"],
        "Image": {
            "ImageElements": [
                {
                    "ImageName": "Mobile",
                    "ImageUrl": "/Assets/images/articles/tile.jpg",
                }
            ]
        }
        if with_image
        else None,
    }


def cba_listing_page(records: Sequence[dict[str, object]]) -> bytes:
    """### Build a releases listing response."""
    return json.dumps({"Data": list(records), "ResponseCode": "200", "Message": ""}, ensure_ascii=False).encode()


def cba_article_html(
    *,
    title: str = "Sample release title for the listings",
    type_label: str | None = "News Releases",
    date_text: str | None = "September 25, 2026",
    topics: Sequence[str] = ("#Mortgages",),
    summary_points: str | None = "A short summary of the release points.",
    sections: Sequence[tuple[str, str]] = RELEASES_SECTIONS,
    pdf_url: str | None = RELEASES_PDF_URL,
    pdf_urls: Sequence[str] | None = None,
    excel_url: str | None = RELEASES_EXCEL_URL,
    with_body: bool = True,
    with_title: bool = True,
) -> bytes:
    """### Build a releases article page mirroring the live markup."""
    badge = f'<div class="newBadge d-inline-block mb-5">{type_label}</div>' if type_label else ""
    date_block = f'<div class="d-inline-block mb-5 articleDateLogo">{date_text}</div>' if date_text else ""
    heading = f'<h1 class="h2 text-white printTitle">{title}</h1>' if with_title else ""
    topic_items = "".join(
        f'<li class="d-inline-block"><a href="/articles?topic=x" class="btn">{topic}</a></li>' for topic in topics
    )
    summary = (
        '<div class="inline-summary pb-4"><div class="overline mb-5 py-3 px-md-2 bt-gray">Summary Points</div>'
        f'<p class="my-2 px-md-2"><time>{summary_points}</time></p></div>'
        if summary_points
        else ""
    )
    resources = ""
    attachments = list(pdf_urls) if pdf_urls is not None else ([pdf_url] if pdf_url else [])
    if attachments or excel_url:
        links = "".join(
            f'<p><a href="{url}" target="_blank">Sample Data {index} (PDF)</a></p>'
            for index, url in enumerate(attachments, start=1)
        )
        if excel_url:
            links += f'<p><a href="{excel_url}" target="_blank">Sample Data (Excel)</a></p>'
        resources = (
            '<div class="d-none d-md-block col-3 sticky-sidebar pe-0">'
            '<div class="overline">Additional information</div>'
            f"{links}</div>"
        )
    body_sections = (
        "".join(f"<h2>{heading_text}</h2><p>{text}</p>" for heading_text, text in sections) if with_body else ""
    )
    document = (
        '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><title>article</title></head><body><main>'
        f'<div class="container-md">{badge}{date_block}{heading}</div>'
        '<div class="mb-5" id="section-to-print">'
        f'<section class="tags"><ul class="m-0 p-0">{topic_items}</ul></section>'
        f"{summary}{resources}{body_sections}"
        "</div></main></body></html>"
    )
    return document.encode()


def detail_html_en(
    *,
    title: str = EN_TITLE,
    paragraphs: Sequence[str] = EN_PARAGRAPHS,
    date_text: str | None = ARTICLE_DATE,
    image_url: str | None = ARTICLE_IMAGE,
    with_container: bool = True,
    with_title_block: bool = True,
) -> bytes:
    """### Build an English UAE article document using the same template."""
    return detail_html(
        title=title,
        paragraphs=paragraphs,
        date_text=date_text,
        image_url=image_url,
        with_container=with_container,
        with_title_block=with_title_block,
    )


def commerce_listing_row(
    *,
    display_date: str | None = "January 2025",
    title: str = "Sample Commerce report",
    slug: str = "sample-commerce-report",
    year: str = "2025",
    month: str = "01",
    pdf_urls: Sequence[str] = COMMERCE_PDF_URLS[:1],
) -> dict[str, object]:
    """### Build one archived Commerce listing row."""
    return {
        "display_date": display_date,
        "title": title,
        "report_url": f"https://www.commerce.gov/data-and-reports/reports/{year}/{month}/{slug}",
        "pdf_urls": list(pdf_urls),
    }


COMMERCE_HOST = "www.commerce.gov"
COMMERCE_LISTING_URL = "https://www.commerce.gov/data-and-reports/reports"
COMMERCE_DETAIL_PREFIX = f"{COMMERCE_LISTING_URL}/"


def commerce_listing_html(
    rows: Sequence[tuple[str, str, str, Sequence[tuple[str, str, str]]]],
    *,
    start: int = 1,
    end: int = 20,
    total: int = 112,
    with_table: bool = True,
) -> str:
    """### Build a Commerce reports listing page with the observed Drupal table markup."""
    body_rows = []
    for date_text, title, detail_path, files in rows:
        file_cells = "".join(
            f'<span class="file {mime}"><a href="{href}" title="{name}" type="{mime}">{name}</a></span>'
            for href, name, mime in files
        )
        body_rows.append(
            "<tr>"
            '<td class="views-field-field-release-datetime">'
            '<strong class="tablesaw-cell-label">Date</strong>'
            f'<span class="tablesaw-cell-content">{date_text}</span></td>'
            '<td class="views-field-title">'
            '<strong class="tablesaw-cell-label">Title</strong>'
            f'<span class="tablesaw-cell-content"><a href="{detail_path}">{title}</a></span></td>'
            '<td class="views-field-field-media-file">'
            '<strong class="tablesaw-cell-label">File(s)</strong>'
            f'<span class="tablesaw-cell-content">{file_cells}</span></td>'
            "</tr>"
        )
    table = (
        '<table class="views-table"><thead><tr>'
        '<th><a href="?sort=asc">Date <span>Sort ascending</span></a></th><th>Title</th><th>File(s)</th>'
        f"</tr></thead><tbody>{''.join(body_rows)}</tbody></table>"
        if with_table
        else "<p>No reports table available</p>"
    )
    return (
        "<html><body><main>"
        f'<div class="view-header">Displaying {start} - {end} of {total}</div>'
        f"{table}"
        "</main></body></html>"
    )


def commerce_detail_html(
    title: str = "Sample Commerce report",
    *,
    date_iso: str = "2025-01-17T18:05:03Z",
    date_text: str = "Friday, January 17, 2025",
    paragraphs: Sequence[str] = (
        "America has the best companies, the best innovators, and the best entrepreneurs in the world.",
        "This report describes the department's work across four years of programmes and results.",
        "It closes with recommendations for the next period of public investment and oversight.",
    ),
    attachments: Sequence[tuple[str, str, str]] = (),
    with_article: bool = True,
    with_title: bool = True,
    with_time: bool = True,
) -> str:
    """### Build a Commerce report detail page with the observed article markup."""
    heading = f"<h1>{title}</h1>" if with_title else ""
    time_block = f'<time class="datetime" datetime="{date_iso}">{date_text}</time>' if with_time else ""
    body = "".join(f"<p>{paragraph}</p>" for paragraph in paragraphs)
    links = "".join(
        f'<span class="file"><a href="{href}" type="{mime}">{name}</a></span>' for href, name, mime in attachments
    )
    article = (
        f'<article data-history-node-id="1"><div>'
        f'<div class="field field--name-field-release-datetime">{time_block}</div>'
        f'<div class="clearfix field field--name-body field--type-text-with-summary">{body}</div>'
        f"<div>{links}</div>"
        f"</div>{heading}</article>"
        if with_article
        else f"<main>{heading}</main>"
    )
    return f"<html><head><title>Report</title></head><body><main>{article}</main></body></html>"
