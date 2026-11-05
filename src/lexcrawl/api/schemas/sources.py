"""### Response schemas for configured sources."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from lexcrawl.db.models import Source


class SourceOut(BaseModel):
    """### Public representation of a configured crawling source."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": 2,
                "code": "cba_news_releases",
                "name": "Canadian Bar Association",
                "base_url": None,
                "is_active": True,
            }
        }
    )

    id: int = Field(description="Source identifier.")
    code: str = Field(description="Stable source code used across the API, e.g. 'cba_news_releases'.")
    name: str = Field(description="Human-readable source name.")
    base_url: str | None = Field(default=None, description="Source website base URL, when configured.")
    is_active: bool = Field(description="Whether the source is enabled for crawling.")

    @classmethod
    def from_model(cls, source: Source) -> SourceOut:
        """### Map a source row to its public representation."""
        return cls(
            id=source.id,
            code=source.code,
            name=source.name,
            base_url=source.base_url,
            is_active=source.is_active,
        )
