"""### Shared retry timing for polite, jittered fetch retries."""

from __future__ import annotations

import random
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime


def parse_retry_after(value: str | None, *, now: datetime | None = None) -> float | None:
    """### Convert Retry-After seconds or an HTTP date into a delay."""
    if value is None:
        return None
    try:
        return max(0.0, float(value.strip()))
    except ValueError:
        pass
    try:
        retry_at = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if retry_at.tzinfo is None:
        retry_at = retry_at.replace(tzinfo=UTC)
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    return max(0.0, (retry_at - current).total_seconds())


def retry_delay(
    attempt: int,
    *,
    backoff_seconds: float,
    retry_after_seconds: float | None = None,
    random_value: float | None = None,
) -> float:
    """### Apply full-jitter exponential backoff, honoring server wait hints."""
    cap = min(30.0, max(0.0, backoff_seconds) * (2 ** max(0, attempt - 1)))
    jitter: float = float(random.random()) if random_value is None else min(1.0, max(0.0, random_value))
    wait_hint: float = 0.0 if retry_after_seconds is None else float(retry_after_seconds)
    return float(max(cap * jitter, wait_hint))
