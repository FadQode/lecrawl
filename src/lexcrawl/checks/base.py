"""### Change-detection contract: a cheap per-source probe and its verdict."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import ClassVar, Literal

# $ The verdict a gate derives from a probe and the stored watermark.$
CheckOutcome = Literal["no_change", "new_data", "check_failed", "anomaly", "budget_exceeded"]


@dataclass(frozen=True, slots=True)
class CheckProbe:
    """### One cheap look at a source that yields a comparable fingerprint."""

    source: str
    ok: bool
    fingerprint: str | None
    summary: str
    duration_seconds: float
    error: str | None = None
    credits_used: int = 0


class Check(ABC):
    """### Source-specific change probe, kept separate from the crawling Actor."""

    name: ClassVar[str]

    @abstractmethod
    def probe(self) -> CheckProbe:
        """### Take one cheap look at the source and return its fingerprint."""
        ...

    def close(self) -> None:
        """### Release any resources the probe holds."""
        return None


def evaluate(probe: CheckProbe, stored_fingerprint: str | None) -> CheckOutcome:
    """### Derive the verdict: fail closed, else compare the fingerprint to the watermark."""
    if not probe.ok:
        return "check_failed"
    if stored_fingerprint is None or stored_fingerprint != probe.fingerprint:
        return "new_data"
    return "no_change"
