"""### Minimal structural samples for parser tests.

These builders emit short documents that mirror the markup and feed contracts verified against
the live source, using reserved example hosts and synthetic text. No archived page is stored.
"""

from __future__ import annotations

import json
from collections.abc import Sequence

RELEASES_API_URL = "https://feed.example.test/content-api"
RELEASES_SITE_URL = "https://releases.example.test"
RELEASES_ARTICLE_PATH = "/article/"
RELEASES_TYPE_ID = "00000000-0000-4000-8000-000000000001"
RELEASES_OTHER_TYPE_ID = "00000000-0000-4000-8000-000000000002"
RELEASES_SECTIONS: tuple[tuple[str, str], ...] = (
    ("First section heading", "First section paragraph."),
    ("Second section heading", "Second section paragraph."),
    ("Third section heading", "Third section paragraph."),
)


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
    """### Build one releases listing record with the verified feed field names."""
    return {
        "SEOName": slug,
        "Title": title,
        "ArticleDate": article_date,
        "PostedDate": posted_date,
        "Summary": summary,
        "ArticleTypes": list(type_ids),
        "ArticleTopics": ["00000000-0000-4000-8000-0000000000ff"],
        "Image": (
            {
                "ImageElements": [
                    {"ImageName": "Mobile", "ImageUrl": "/Assets/images/articles/tile.jpg"},
                ]
            }
            if with_image
            else None
        ),
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
    body_sections = (
        "".join(f"<h2>{heading_text}</h2><p>{text}</p>" for heading_text, text in sections) if with_body else ""
    )
    document = (
        '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><title>article</title></head><body><main>'
        f'<div class="container-md">{badge}{date_block}{heading}</div>'
        '<div class="mb-5" id="section-to-print">'
        f'<section class="tags"><ul class="m-0 p-0">{topic_items}</ul></section>'
        f"{summary}{body_sections}"
        "</div></main></body></html>"
    )
    return document.encode()


COMMERCE_HOST = "www.commerce.test"
COMMERCE_LISTING_URL = f"https://{COMMERCE_HOST}/data-and-reports/reports"
COMMERCE_LISTING_PATH = "/data-and-reports/reports"
COMMERCE_PDF_URL = "https://www.commerce.test/sites/default/files/2025-01/sample-report.pdf"


def commerce_row(
    *,
    date_text: str = "January 2025",
    title: str = "Sample Commerce report",
    slug: str = "sample-commerce-report",
    year: str = "2025",
    month: str = "01",
    pdf_urls: Sequence[str] = (COMMERCE_PDF_URL,),
) -> tuple[str, str, str, Sequence[str]]:
    """### Build one listing row: (date text, title, href, attachment URLs)."""
    href = f"{COMMERCE_LISTING_PATH}/{year}/{month}/{slug}"
    return (date_text, title, href, list(pdf_urls))


def commerce_listing_html(
    rows: Sequence[tuple[str, str, str, Sequence[str]]],
    *,
    start: int = 1,
    end: int | None = None,
    total: int | None = None,
    with_table: bool = True,
) -> str:
    """### Build a reports listing page using the observed table markup."""
    body_rows = []
    for date_text, title, href, pdf_urls in rows:
        files = "".join(f'<a href="{url}" title="{url.rsplit("/", 1)[-1]}">PDF</a>' for url in pdf_urls)
        body_rows.append(
            "<tr>"
            f'<td class="views-field"><div class="tablesaw-cell-content">{date_text}</div></td>'
            f'<td class="views-field"><div class="tablesaw-cell-content"><a href="{href}">{title}</a></div></td>'
            f'<td class="views-field"><div class="tablesaw-cell-content">{files}</div></td>'
            "</tr>"
        )
    table = (
        "<table><thead><tr><th>Date</th><th>Title</th><th>File(s)</th></tr></thead>"
        f"<tbody>{''.join(body_rows)}</tbody></table>"
        if with_table
        else ""
    )
    resolved_end = end if end is not None else len(rows)
    resolved_total = total if total is not None else len(rows)
    range_block = (
        f'<div class="view-header"><span>Displaying {start} - {resolved_end} of {resolved_total}</span></div>'
    )
    return (
        '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><title>reports</title></head><body><main>'
        f"{range_block}{table}"
        "</main></body></html>"
    )


def commerce_detail_html(
    *,
    title: str = "Sample Commerce report",
    published: str | None = "2025-01-15T00:00:00Z",
    paragraphs: Sequence[str] = (
        "The Department published the sample report about the programme and its findings for the year.",
        "The report describes the methodology, the results, and the recommendations for the next period.",
    ),
    pdf_urls: Sequence[str] = (COMMERCE_PDF_URL,),
    with_article: bool = True,
    with_title: bool = True,
) -> str:
    """### Build a report detail page mirroring the live article markup."""
    heading = f"<h1>{title}</h1>" if with_title else ""
    time_block = f'<time datetime="{published}">January 15, 2025</time>' if published else ""
    body = "".join(f"<p>{paragraph}</p>" for paragraph in paragraphs)
    attachments = "".join(f'<a href="{url}">Attachment</a>' for url in pdf_urls)
    article = (
        f"<article>{heading}{time_block}"
        f'<div class="field--name-body">{body}</div>'
        f'<div class="field--name-field-files">{attachments}</div>'
        "</article>"
        if with_article
        else ""
    )
    return (
        '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><title>report</title></head>'
        f"<body><main>{article}</main></body></html>"
    )


UAE_HOST = "listing.example.test"
UAE_LISTING_URL = f"https://{UAE_HOST}/ar/news"
UAE_ARTICLE_PREFIX = "/ar/news/"
UAE_PAGE_TWO_URL = f"{UAE_LISTING_URL}?page=2"

UAE_TITLE = "إعلان تجريبي: تفاصيل الخبر الأول عن المنظومة الجديدة"
UAE_PARAGRAPHS: tuple[str, ...] = (
    "أعلنت الجهة المعنية عن تفاصيل الخبر الأول، مشيرة إلى أن المنظومة الجديدة تدخل حيز التنفيذ.",
    "وأوضحت أن المرحلة التالية تشمل تبادل الخبرات ومراجعة النصوص ذات الصلة.",
)


def uae_listing_html(
    *,
    cards: int = 3,
    next_page: str | None = UAE_PAGE_TWO_URL,
    duplicate_first: bool = False,
) -> bytes:
    """### Build a UAE listing page using the observed card markup."""
    items: list[str] = []
    for index in range(1, cards + 1):
        summary = "" if index == 1 else f'<div class="desc_">ملخص الخبر رقم {index}</div>'
        items.append(
            '<div class="item_ animated_wrapper">'
            f'<a class="link_" href="{UAE_ARTICLE_PREFIX}article-{index}">'
            f'<div class="image_box"><div class="img_"><img src="/storage/post/large/{index}.png"/></div></div>'
            '<div class="content_">'
            f"<div class=\"dateBox\"><span>Jan {index:02d} 2026</span></div>"
            f'<div class="title_">عنوان الخبر رقم {index}</div>'
            f"{summary}"
            "</div></a></div>"
        )
    if duplicate_first and items:
        items.append(items[0])
    pagination = (
        '<div class="table_pagination"><ul>'
        f'<li><a class="page-link l_ next_" href="{next_page}">next</a></li>'
        "</ul></div>"
        if next_page
        else ""
    )
    document = (
        '<!DOCTYPE html><html lang="ar" dir="rtl"><head><meta charset="utf-8"><title>news</title></head>'
        f"<body><main>{''.join(items)}{pagination}</main></body></html>"
    )
    return document.encode()


def uae_detail_html(
    *,
    title: str = UAE_TITLE,
    paragraphs: Sequence[str] = UAE_PARAGRAPHS,
    date_text: str | None = "Jan 08 2026",
    image_url: str | None = "https://listing.example.test/storage/post/large/1.png",
    with_title: bool = True,
    with_body: bool = True,
) -> bytes:
    """### Build a UAE article page using the observed detail markup."""
    title_block = f'<div class="section_title"><h2>{title}</h2></div>' if with_title else ""
    date_block = f'<div class="dateBox"><span>{date_text}</span></div>' if date_text else ""
    image_block = f'<div class="imgBox"><img src="{image_url}"/></div>' if image_url else ""
    body = "".join(f"<p>{paragraph}</p>" for paragraph in paragraphs) if with_body else ""
    container = f'<div class="newsDetailBox">{date_block}{image_block}<div class="textBox">{body}</div></div>'
    document = (
        '<!DOCTYPE html><html lang="ar" dir="rtl"><head><meta charset="utf-8"><title>article</title></head>'
        f"<body><main>{title_block}{container}</main></body></html>"
    )
    return document.encode()
