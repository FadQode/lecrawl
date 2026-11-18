"""### Unit tests for the metrics registry and the /metrics endpoint."""

from __future__ import annotations

from fastapi.testclient import TestClient

from lexcrawl.api.app import create_app
from lexcrawl.config import Settings
from lexcrawl.metrics import METRICS, MetricsRegistry, record_source_run


def test_registry_renders_prometheus_text() -> None:
    """### Counters and gauges render with HELP/TYPE and labels."""
    registry = MetricsRegistry()
    registry.inc("lexcrawl_x_total", help_text="x counter", labels={"a": "b"})
    registry.inc("lexcrawl_x_total", help_text="x counter", labels={"a": "b"}, value=2)
    registry.set("lexcrawl_y", 3.0, help_text="y gauge")
    text = registry.render()
    assert "# HELP lexcrawl_x_total x counter" in text
    assert "# TYPE lexcrawl_x_total counter" in text
    assert 'lexcrawl_x_total{a="b"} 3' in text
    assert "# TYPE lexcrawl_y gauge" in text
    assert "lexcrawl_y 3\n" in text


def test_record_source_run_updates_registry() -> None:
    """### One source run records runs, duration, credits, and failures."""
    record_source_run(source="metrics-test", status="success", duration_seconds=1.5, credits=2, failed=1)
    text = METRICS.render()
    assert 'lexcrawl_source_runs_total{source="metrics-test",status="success"} 1' in text
    assert 'lexcrawl_credits_total{source="metrics-test"} 2' in text
    assert 'lexcrawl_failures_total{source="metrics-test"} 1' in text


def test_metrics_endpoint_without_database() -> None:
    """### /metrics is served without a database and without authentication."""
    settings = Settings(_env_file=None, database_url="", db_host="")
    with TestClient(create_app(settings)) as client:
        response = client.get("/metrics")
        assert response.status_code == 200
        assert "text/plain" in response.headers["content-type"]
        assert "lexcrawl_source_runs_total" in response.text
