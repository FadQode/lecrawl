"""### OpenAPI metadata, tags, operation ids, and shared error responses."""

from __future__ import annotations

from typing import Any

from fastapi.routing import APIRoute

from lexcrawl.api.schemas.common import ProblemDetail

DESCRIPTION = (
    "LexCrawl is a multi-source crawling and content-extraction platform. It discovers content "
    "from configured sources, extracts and normalizes it, and exposes the collected content "
    "through this read API.\n\n"
    "Public resources: **articles** (news articles and news releases), **reports** "
    "(reports and publications), **resources** (PDFs and other linked files), **sources**, and "
    "**crawl runs**. The API is content-oriented; internal document terminology is not exposed.\n\n"
    "All endpoints are read-only and paginated. Errors use RFC 9457 `application/problem+json`. "
    "Crawl triggering is not part of this API version."
)

TAGS: list[dict[str, str]] = [
    {"name": "System", "description": "Liveness and readiness probes."},
    {"name": "Sources", "description": "Websites configured for crawling."},
    {"name": "Articles", "description": "News articles and news releases."},
    {"name": "Reports", "description": "Reports and publications."},
    {"name": "Resources", "description": "PDFs, attachments, and other linked files."},
    {"name": "Crawl Runs", "description": "Crawl execution history, status, and errors."},
]


def _problem(description: str) -> dict[str, Any]:
    """### Build one Problem Details response entry for the OpenAPI document."""
    return {
        "description": description,
        "content": {"application/problem+json": {"schema": ProblemDetail.model_json_schema()}},
    }


NOT_FOUND_RESPONSE = _problem("The requested resource does not exist.")
VALIDATION_RESPONSE = _problem("One or more request parameters failed validation.")
UNAUTHORIZED_RESPONSE = _problem("Missing or invalid bearer token.")
CONFLICT_RESPONSE = _problem("A conflicting crawl is already active for the requested source.")
UNAVAILABLE_RESPONSE = _problem("A required dependency (PostgreSQL) is unavailable.")
INTERNAL_RESPONSE = _problem("An unexpected internal error occurred.")

# $ Common combinations so routes document only the codes they can actually produce.$
VALIDATION_ONLY: dict[int | str, dict[str, Any]] = {422: VALIDATION_RESPONSE}
READ_BY_ID: dict[int | str, dict[str, Any]] = {404: NOT_FOUND_RESPONSE, 422: VALIDATION_RESPONSE}
TRIGGER_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: UNAUTHORIZED_RESPONSE,
    409: CONFLICT_RESPONSE,
    422: VALIDATION_RESPONSE,
    503: UNAVAILABLE_RESPONSE,
}


def operation_id(route: APIRoute) -> str:
    """### Derive a stable operation id from the route handler function name."""
    return str(route.name)


def clean_docstring_markers(schema: dict[str, Any]) -> dict[str, Any]:
    """### Strip the internal `###` docstring marker from public descriptions."""

    def walk(node: Any) -> None:
        """### Recurse through the schema, cleaning description strings."""
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "description" and isinstance(value, str):
                    node[key] = value.removeprefix("### ").strip()
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(schema)
    return schema
