"""### In-process metrics registry with a Prometheus text exposition.

Deliberately dependency-free: the exposed surface is a small set of counters and
gauges rendered in the Prometheus text format at `/metrics`.
"""

from __future__ import annotations

import threading

_METRIC_TYPES = ("counter", "gauge")


class MetricsRegistry:
    """### A thread-safe registry of labelled counters and gauges."""

    def __init__(self) -> None:
        """### Start with an empty registry."""
        self._lock = threading.Lock()
        self._values: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
        self._types: dict[str, str] = {}
        self._help: dict[str, str] = {}

    def _declare(self, name: str, metric_type: str, help_text: str) -> None:
        """### Record a metric's type and help text (first declaration wins)."""
        if metric_type not in _METRIC_TYPES:
            raise ValueError(f"unknown metric type {metric_type!r}")
        self._types.setdefault(name, metric_type)
        self._help.setdefault(name, help_text)

    def inc(self, name: str, *, help_text: str = "", labels: dict[str, str] | None = None, value: float = 1.0) -> None:
        """### Increment a counter by `value`."""
        with self._lock:
            self._declare(name, "counter", help_text or name)
            key = (name, tuple(sorted((labels or {}).items())))
            self._values[key] = self._values.get(key, 0.0) + value

    def set(self, name: str, value: float, *, help_text: str = "", labels: dict[str, str] | None = None) -> None:
        """### Set a gauge to an absolute value."""
        with self._lock:
            self._declare(name, "gauge", help_text or name)
            key = (name, tuple(sorted((labels or {}).items())))
            self._values[key] = value

    def render(self) -> str:
        """### Render every metric in the Prometheus text exposition format."""
        with self._lock:
            values = dict(self._values)
            types = dict(self._types)
            help_text = dict(self._help)
        lines: list[str] = []
        for name in sorted(types):
            lines.append(f"# HELP {name} {help_text[name]}")
            lines.append(f"# TYPE {name} {types[name]}")
            for (metric, labels), value in sorted(values.items()):
                if metric != name:
                    continue
                rendered = _render_labels(dict(labels))
                lines.append(f"{name}{rendered} {_render_value(value)}")
        return "\n".join(lines) + "\n"


def _render_labels(labels: dict[str, str]) -> str:
    """### Render a label set, escaping values, or an empty string when unlabelled."""
    if not labels:
        return ""
    inner = ",".join(f'{key}="{_escape_label(value)}"' for key, value in sorted(labels.items()))
    return "{" + inner + "}"


def _escape_label(value: str) -> str:
    """### Escape backslashes, quotes, and newlines in a label value."""
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _render_value(value: float) -> str:
    """### Render a numeric value as an integer when it is whole."""
    if value.is_integer():
        return str(int(value))
    return repr(value)


# $ One process-wide registry: counters are cheap and the worker is single-process per engine.$
METRICS = MetricsRegistry()


def record_source_run(
    *, source: str, status: str, duration_seconds: float, credits: int, failed: int
) -> None:
    """### Record one source's run outcome, duration, credits, and failures."""
    METRICS.inc(
        "lexcrawl_source_runs_total",
        help_text="Crawl runs per source and terminal status.",
        labels={"source": source, "status": status},
    )
    METRICS.inc(
        "lexcrawl_source_duration_seconds_sum",
        help_text="Total crawl time per source, in seconds.",
        labels={"source": source},
        value=duration_seconds,
    )
    METRICS.inc(
        "lexcrawl_source_duration_seconds_count",
        help_text="Number of timed crawl runs per source.",
        labels={"source": source},
    )
    METRICS.inc(
        "lexcrawl_credits_total",
        help_text="Provider credits spent per source.",
        labels={"source": source},
        value=credits,
    )
    METRICS.inc(
        "lexcrawl_failures_total",
        help_text="Failed documents per source.",
        labels={"source": source},
        value=failed,
    )
