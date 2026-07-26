"""Transient-error retry — the "task stops midway on failure" fix.

Real dogfood bug: on a flaky/slow reseller endpoint, a single transient API
error (429/5xx/timeout) mid-task killed the whole turn. The REPL had retry-with-
backoff but the TUI did not. This locks in:

  1. is_transient_error: classifies 429/5xx/timeout/connection as retryable.
  2. TuiApp._retry_transient: retries up to 3x, returns recovered (non-terminal)
     on success, terminal on exhaustion/non-transient.

IPR-0: each test includes a counterexample.
"""

from __future__ import annotations

import pytest


class TestIsTransientError:
    def test_classification(self) -> None:
        from zall.cli.repl_ui import is_transient_error
        # transient
        assert is_transient_error("API error (HTTP 429): rate limited")
        assert is_transient_error("API error (HTTP 503)")
        assert is_transient_error("read timeout after 120s")
        assert is_transient_error("connection reset")
        # counterexample: non-transient / empty must be False
        assert not is_transient_error("API error (HTTP 401): invalid key")
        assert not is_transient_error("model refused")
        assert not is_transient_error(None)
        assert not is_transient_error("")


# ── minimal StepResult-like fakes (avoid real model / event loop) ──

class _Egress:
    def __init__(self, error: str | None) -> None:
        self.error = error


class _Step:
    def __init__(self, terminal: bool, error: str | None = None, kind: str = "tool_used") -> None:
        self._terminal = terminal
        self.kind = "terminal" if terminal else kind
        self.egress = _Egress(error) if terminal else None

    @property
    def is_terminal(self) -> bool:
        return self._terminal


class _ScriptedLoop:
    """retry_step returns a scripted sequence of results."""
    def __init__(self, results: list) -> None:
        self._results = results
        self.calls = 0

    def retry_step(self):
        r = self._results[min(self.calls, len(self._results) - 1)]
        self.calls += 1
        return r


def _make_app(monkeypatch):
    import time as _time
    monkeypatch.setattr(_time, "sleep", lambda *_a, **_k: None)  # no real backoff delay
    pytest.importorskip("textual")
    from zall.cli.tui import TuiApp
    app = TuiApp()
    # no-op UI bridge (app is not mounted / no event loop)
    app.call_from_thread = lambda fn, *a, **k: None  # type: ignore[assignment]
    app._interrupt_requested = False
    return app


class TestRetryTransient:
    def test_recovers_on_second_attempt(self, monkeypatch) -> None:
        app = _make_app(monkeypatch)
        first = _Step(terminal=True, error="API error (HTTP 429): rate limited")
        recovered = _Step(terminal=False, kind="tool_used")
        loop = _ScriptedLoop([recovered])
        out = app._retry_transient(loop, first)
        assert not out.is_terminal          # recovered → task continues
        assert loop.calls == 1              # one retry sufficed

    def test_exhausts_after_3_when_persistent(self, monkeypatch) -> None:
        app = _make_app(monkeypatch)
        first = _Step(terminal=True, error="API error (HTTP 503)")
        # every retry still transient-terminal
        loop = _ScriptedLoop([_Step(terminal=True, error="API error (HTTP 503)")])
        out = app._retry_transient(loop, first)
        assert out.is_terminal              # still failing
        assert loop.calls == 3              # counterexample: NOT infinite; bounded at 3

    def test_stops_early_on_non_transient(self, monkeypatch) -> None:
        app = _make_app(monkeypatch)
        first = _Step(terminal=True, error="API error (HTTP 500)")
        # retry returns a NON-transient terminal → must stop immediately (no more retries)
        loop = _ScriptedLoop([_Step(terminal=True, error="API error (HTTP 401): invalid key")])
        out = app._retry_transient(loop, first)
        assert out.is_terminal
        assert loop.calls == 1              # counterexample: did NOT keep retrying a fatal error

    def test_interrupt_aborts_retry(self, monkeypatch) -> None:
        app = _make_app(monkeypatch)
        app._interrupt_requested = True
        first = _Step(terminal=True, error="API error (HTTP 429)")
        loop = _ScriptedLoop([_Step(terminal=False)])
        out = app._retry_transient(loop, first)
        assert out is first                 # aborted before any retry
        assert loop.calls == 0
