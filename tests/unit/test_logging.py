"""### Structured logging tests."""

from __future__ import annotations

import json
import logging

from lexcrawl.logging import JsonFormatter, configure_logging, get_logger


def build_record(**extra: object) -> logging.LogRecord:
    """### Build a log record with structured extras."""
    record = logging.LogRecord(
        name="lexcrawl.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="crawl_step",
        args=(),
        exc_info=None,
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_json_formatter_emits_structured_fields() -> None:
    """### Records serialize to one JSON object including extras."""
    payload = json.loads(JsonFormatter().format(build_record(source="uae_legislation_news", documents=20)))
    assert payload["level"] == "INFO"
    assert payload["logger"] == "lexcrawl.test"
    assert payload["message"] == "crawl_step"
    assert payload["source"] == "uae_legislation_news"
    assert payload["documents"] == 20
    assert payload["timestamp"]


def test_json_formatter_keeps_non_ascii_text_readable() -> None:
    """### Arabic content is not escaped away."""
    payload = JsonFormatter().format(build_record(title="مريم الحمادي"))
    assert "مريم الحمادي" in payload


def test_json_formatter_serializes_unserializable_values() -> None:
    """### Values that JSON cannot encode fall back to their string form."""
    payload = json.loads(JsonFormatter().format(build_record(target=object())))
    assert payload["target"].startswith("<object object at")


def test_configure_logging_uses_json_by_default() -> None:
    """### The default handler is the JSON formatter."""
    configure_logging("DEBUG", "json")
    root = logging.getLogger()
    assert root.level == logging.DEBUG
    assert isinstance(root.handlers[0].formatter, JsonFormatter)


def test_configure_logging_supports_console_format() -> None:
    """### The console format is available for local runs."""
    configure_logging("WARNING", "console")
    root = logging.getLogger()
    assert root.level == logging.WARNING
    assert not isinstance(root.handlers[0].formatter, JsonFormatter)
    assert get_logger("lexcrawl.example").name == "lexcrawl.example"
