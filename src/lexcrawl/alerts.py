"""### Alert hook: emit structured alerts now, pluggable into a notifier later."""

from __future__ import annotations

from lexcrawl.logging import get_logger

LOGGER = get_logger("lexcrawl.alerts")

# $ Events that warrant operator attention rather than routine logging.$
ANOMALY = "anomaly"
BUDGET_EXCEEDED = "budget_exceeded"
CHECK_FAILED = "check_failed"


def notify(event: str, message: str, **fields: object) -> None:
    """### Emit one structured alert at WARNING level with its context."""
    LOGGER.warning(event, extra={"alert": event, "detail": message, **fields})
