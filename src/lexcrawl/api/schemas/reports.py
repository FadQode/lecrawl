"""### Response schemas for reports and publications."""

from __future__ import annotations

from typing import Literal

from lexcrawl.api.schemas.articles import ArticleDetail, ArticleSummary


class ReportSummary(ArticleSummary):
    """### Public summary of a report or publication."""

    kind: Literal["report"] = "report"


class ReportDetail(ArticleDetail):
    """### Full public representation of a report and its content."""

    kind: Literal["report"] = "report"
