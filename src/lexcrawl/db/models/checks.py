"""### Small helpers shared by the ORM models."""

from __future__ import annotations


def sql_in(values: tuple[str, ...]) -> str:
    """### Render a SQL IN list for a named CHECK constraint."""
    rendered = ", ".join(f"'{value}'" for value in values)
    return f"IN ({rendered})"


def non_negative(column: str) -> str:
    """### Render a non-negative CHECK expression for a column."""
    return f"{column} >= 0"


def timestamp_order(start: str, end: str) -> str:
    """### Render a CHECK ensuring an end timestamp is not before its start."""
    return f"{end} IS NULL OR {end} >= {start}"
