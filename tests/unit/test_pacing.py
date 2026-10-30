"""### Request pacing tests."""

from __future__ import annotations

from lexcrawl.crawling.pacing import RequestPacer

from ..conftest import ClockRecorder


def test_pacer_does_not_sleep_when_no_interval_is_configured(clock: ClockRecorder) -> None:
    """### A zero interval leaves request timing untouched."""
    pacer = RequestPacer(0, sleep=clock.sleep, monotonic=clock.monotonic)
    pacer.wait_for_slot()
    pacer.wait_for_slot()
    assert clock.slept == []


def test_pacer_separates_consecutive_requests(clock: ClockRecorder) -> None:
    """### The first request is immediate and the second waits the interval."""
    pacer = RequestPacer(2.5, sleep=clock.sleep, monotonic=clock.monotonic)
    pacer.wait_for_slot()
    assert clock.slept == []
    pacer.wait_for_slot()
    assert clock.slept == [2.5]


def test_pacer_only_waits_the_remaining_interval(clock: ClockRecorder) -> None:
    """### Already elapsed time counts against the interval."""
    pacer = RequestPacer(2.0, sleep=clock.sleep, monotonic=clock.monotonic)
    pacer.wait_for_slot()
    clock.sleep(1.5)
    clock.slept.clear()
    pacer.wait_for_slot()
    assert clock.slept == [0.5]


def test_pacer_exposes_its_interval() -> None:
    """### The configured interval is readable for diagnostics."""
    assert RequestPacer(1.25).min_interval_seconds == 1.25
    assert RequestPacer(-5).min_interval_seconds == 0.0
