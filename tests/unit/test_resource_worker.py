"""### Unit test for the resource worker's process-pool extractor."""

from __future__ import annotations

from lexcrawl.resource_worker import _pool_extractor


class _FakeExecutor:
    """### Run a map synchronously and record the calls."""

    def __init__(self) -> None:
        """### Start with no recorded calls."""
        self.calls: list[tuple[object, list[object]]] = []

    def map(self, fn: object, iterable: object) -> list[object]:
        """### Apply `fn` to each item, mirroring `ProcessPoolExecutor.map` order."""
        items = list(iterable)  # type: ignore[call-overload]
        self.calls.append((fn, items))
        return [fn(item) for item in items]  # type: ignore[operator]


def test_pool_extractor_submits_the_whole_batch() -> None:
    """### The extractor maps the entire batch through the pool and returns ordered results."""
    executor = _FakeExecutor()
    extract = _pool_extractor(executor)  # type: ignore[arg-type]
    payloads = [b"%PDF-1.7 a", b"%PDF-1.7 b"]
    results = extract(payloads)
    assert len(results) == 2
    assert executor.calls and executor.calls[0][1] == payloads
