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
