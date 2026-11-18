"""### Unit tests for the daily-run orchestration (fakes, no database)."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from lexcrawl.actors.base import ActorRunResult
from lexcrawl.checks import runner
from lexcrawl.checks.base import CheckProbe
from lexcrawl.checks.checkpoints import CheckpointState
from lexcrawl.checks.identities import RememberResult
from lexcrawl.config import Settings
from lexcrawl.locking import RunInProgressError


@dataclass
class _Handle:
    """### A recorded run handle."""

    run_id_internal: int = 1
    run_id: str = "run-1"


class _FakeBudget:
    """### Records committed credits and budget writes."""

    def __init__(self) -> None:
        self.recorded: list[tuple[str, int]] = []

    def committed(self, period: str) -> int:
        return 0

    def record(self, period: str, credits: int) -> None:
        self.recorded.append((period, credits))


class _FakeCheckpoints:
    """### In-memory checkpoint store."""

    def __init__(self, *, processed: str | None = None) -> None:
        self._state = CheckpointState(processed_fingerprint=processed)
        self.advanced: list[tuple[str, str | None]] = []

    def get(self, source: str) -> CheckpointState:
        return self._state

    def record_observed(self, source: str, fingerprint: str | None, outcome: str) -> None:
        return None

    def advance_processed(self, source: str, fingerprint: str | None) -> None:
        self.advanced.append((source, fingerprint))

    def record_failure(self, source: str, error: str) -> None:
        return None

    def close(self) -> None:
        return None


class _FakeMemory:
    """### In-memory identity memory."""

    def __init__(self, *, known: frozenset[str] = frozenset(), due: int = 0, baseline: bool = False) -> None:
        self._known = known
        self._due = due
        self._baseline = baseline
        self.remembered = 0
        self.retried = False

    def known(self, source: str) -> frozenset[str]:
        return self._known

    def filter_known(self, source: str, keys: object) -> frozenset[str]:
        return frozenset(key for key in keys if key in self._known)  # type: ignore[union-attr]

    def has_baseline(self, source: str) -> bool:
        return self._baseline

    def due_count(self, source: str) -> int:
        return self._due

    def retry_due(self, source: str) -> int:
        self.retried = True
        return 0

    def remember(self, source: str, documents: object) -> RememberResult:
        self.remembered = len(documents)  # type: ignore[arg-type]
        return RememberResult(documents=self.remembered, inserted=self.remembered)

    def close(self) -> None:
        return None


class _FakeRecorder:
    """### Records start/source/finish calls."""

    def __init__(self) -> None:
        self.started: tuple[str, str | None] | None = None
        self.sources: list[str] = []
        self.finished: tuple[str, int] | None = None

    def start(self, trigger: str, *, run_id: str | None = None) -> _Handle:
        self.started = (trigger, run_id)
        return _Handle()

    def record_source(self, run_id_internal: int, source: str, **kwargs: object) -> int:
        self.sources.append(source)
        return 1

    def record_errors(self, source_run_id: int, errors: object) -> None:
        return None

    def finish(self, run_id_internal: int, *, status: str, credits: int, duration_ms: int) -> None:
        self.finished = (status, credits)

    def close(self) -> None:
        return None


@dataclass
class _FakeRuntime:
    """### Fake persistence runtime shared by the run."""

    checkpoints: object
    memory: object
    recorder: object
    engine: object = field(default_factory=object)
    closed: bool = False

    def close(self) -> None:
        self.closed = True


class _FakeFetcher:
    """### A fetcher that does nothing."""

    def close(self) -> None:
        return None


class _FakeCheck:
    """### A check returning a fixed probe."""

    name = "uae"

    def __init__(self, probe: CheckProbe) -> None:
        self._probe = probe

    def probe(self) -> CheckProbe:
        return self._probe

    def close(self) -> None:
        return None


class _FakeActor:
    """### An actor returning a canned run result."""

    name = "uae"

    def __init__(self, result: ActorRunResult) -> None:
        self._result = result

    def run(self, fetcher: object) -> ActorRunResult:
        return self._result


def _probe(fingerprint: str = "fp", *, ok: bool = True) -> CheckProbe:
    """### Build a check probe."""
    return CheckProbe(source="uae", ok=ok, fingerprint=fingerprint, summary="s", duration_seconds=0.0)


def _install(
    monkeypatch: pytest.MonkeyPatch,
    *,
    runtime: _FakeRuntime,
    probe: CheckProbe,
    actor_result: ActorRunResult | None = None,
    scraper: object | None = None,
) -> _FakeBudget:
    """### Wire the runner to fakes and return the budget."""
    budget = _FakeBudget()

    def fake_actor(name: str, settings: Settings, **kwargs: object) -> _FakeActor:
        return _FakeActor(actor_result or ActorRunResult(source=name))

    monkeypatch.setattr(runner, "build_runtime", lambda settings, engine=None: runtime)
    monkeypatch.setattr(runner, "build_credit_budget", lambda engine: budget)
    monkeypatch.setattr(runner, "build_scraper", lambda name, settings, **kwargs: scraper)
    monkeypatch.setattr(runner, "build_http_fetcher", lambda settings: _FakeFetcher())
    monkeypatch.setattr(runner, "build_check", lambda name, settings, **kwargs: _FakeCheck(probe))
    monkeypatch.setattr(runner, "build_actor", fake_actor)
    return budget


def _settings() -> Settings:
    """### Minimal settings for a run."""
    return Settings(_env_file=None, listings_url="https://uae.test/news")


def _run_result(source: str = "uae", **overrides: object) -> ActorRunResult:
    """### Build a completed actor result."""
    result = ActorRunResult(source=source)
    result.documents = [object()]  # type: ignore[list-item]
    result.pagination_complete = True
    for key, value in overrides.items():
        setattr(result, key, value)
    return result


def test_no_change_skips_the_crawl(monkeypatch: pytest.MonkeyPatch) -> None:
    """### A no-change probe returns success without running the actor."""
    runtime = _FakeRuntime(_FakeCheckpoints(processed="fp"), _FakeMemory(), _FakeRecorder())
    _install(monkeypatch, runtime=runtime, probe=_probe("fp"))
    results = runner.run_daily(_settings(), sources=("uae",), record=False)
    assert len(results) == 1 and results[0].status == "success" and results[0].outcome == "no_change"
    assert results[0].documents == 0
    assert runtime.closed is True


def test_new_data_runs_actor_and_records(monkeypatch: pytest.MonkeyPatch) -> None:
    """### New data runs the actor, remembers documents, and advances the watermark."""
    checkpoints = _FakeCheckpoints(processed="old")
    memory = _FakeMemory()
    recorder = _FakeRecorder()
    runtime = _FakeRuntime(checkpoints, memory, recorder)
    budget = _install(monkeypatch, runtime=runtime, probe=_probe("new"), actor_result=_run_result())
    monkeypatch.setattr(runner, "AdvisoryLock", _FakeLock)
    results = runner.run_daily(_settings(), sources=("uae",), record=True, run_id="job-1")
    assert results[0].status == "success" and results[0].inserted == 1
    assert memory.remembered == 1 and checkpoints.advanced == [("uae", "new")]
    assert recorder.started == ("manual", "job-1") and recorder.finished == ("success", 0)
    assert budget.recorded == []


def test_single_source_lock_busy_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """### A single-source run raises when its lock is held (worker requeues)."""
    runtime = _FakeRuntime(_FakeCheckpoints(), _FakeMemory(), _FakeRecorder())
    _install(monkeypatch, runtime=runtime, probe=_probe("new"))
    monkeypatch.setattr(runner, "AdvisoryLock", _BusyLock)
    with pytest.raises(RunInProgressError):
        runner.run_daily(_settings(), sources=("uae",), record=True)
    assert runtime.closed is True


def test_multi_source_busy_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    """### A contended source is reported partial without aborting the run."""
    runtime = _FakeRuntime(_FakeCheckpoints(), _FakeMemory(), _FakeRecorder())
    _install(monkeypatch, runtime=runtime, probe=_probe("new"))
    monkeypatch.setattr(runner, "AdvisoryLock", _BusyLock)
    results = runner.run_daily(_settings(), sources=("uae", "cba"), record=True)
    assert {result.status for result in results} == {"partial_failure"}


def test_check_failed_is_recorded(monkeypatch: pytest.MonkeyPatch) -> None:
    """### A failed probe yields a failed source result."""
    runtime = _FakeRuntime(_FakeCheckpoints(), _FakeMemory(), _FakeRecorder())
    _install(monkeypatch, runtime=runtime, probe=_probe("fp", ok=False))
    results = runner.run_daily(_settings(), sources=("uae",), record=False)
    assert results[0].status == "failed" and results[0].outcome == "check_failed"


def test_due_documents_trigger_crawl_without_new_data(monkeypatch: pytest.MonkeyPatch) -> None:
    """### A no-change probe still crawls when documents are due for retry."""
    runtime = _FakeRuntime(_FakeCheckpoints(processed="fp"), _FakeMemory(due=3), _FakeRecorder())
    _install(monkeypatch, runtime=runtime, probe=_probe("fp"), actor_result=_run_result())
    results = runner.run_daily(_settings(), sources=("uae",), record=False)
    assert results[0].documents == 1


def test_anomaly_halts_crawl(monkeypatch: pytest.MonkeyPatch) -> None:
    """### An anomaly-marked actor result becomes an anomaly outcome."""
    result = _run_result(anomaly_detected=True)
    runtime = _FakeRuntime(_FakeCheckpoints(processed="old"), _FakeMemory(baseline=True), _FakeRecorder())
    _install(monkeypatch, runtime=runtime, probe=_probe("new"), actor_result=result)
    monkeypatch.setattr(runner, "AdvisoryLock", _FakeLock)
    settings = Settings(_env_file=None, listings_url="https://uae.test/news", anomaly_max_new_items=1)
    results = runner.run_daily(settings, sources=("uae",), record=True)
    assert results[0].outcome == "anomaly" and results[0].status == "partial_failure"


def test_anomaly_limit_only_with_baseline() -> None:
    """### The anomaly limit applies only on incremental runs with a baseline."""
    settings = Settings(_env_file=None, anomaly_max_new_items=5)
    assert runner._anomaly_limit(settings, has_baseline=True, bypass=False) == 5
    assert runner._anomaly_limit(settings, has_baseline=False, bypass=False) is None
    assert runner._anomaly_limit(settings, has_baseline=True, bypass=True) is None


class _FakeLock:
    """### An advisory lock that always acquires."""

    def __init__(self, engine: object, key: int = 0) -> None:
        self.key = key

    def acquire(self) -> bool:
        return True

    def release(self) -> None:
        return None


class _BusyLock(_FakeLock):
    """### An advisory lock that is already held."""

    def acquire(self) -> bool:
        return False
