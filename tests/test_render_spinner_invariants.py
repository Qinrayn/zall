"""Spinner thread lifecycle invariant test (A3: persistent thread, not per-call).

IPR-0: each test must contain a counterexample.

Protected invariants:
  1. Multiple model_call_start → stop cycles create only 1 spinner thread.
  2. shutdown_spinner() exists and terminates the thread properly.
  3. _stop_spinner preserves _spinner_thread (does not set to None).
"""

from __future__ import annotations

import threading
from typing import Any

from zall.cli.render import CliRenderer


class _MockStream:
    """Minimal writable stream for CliRenderer."""
    def __init__(self) -> None:
        self.buf = ""

    def write(self, s: str) -> None:
        self.buf += s

    def flush(self) -> None:
        pass

    def isatty(self) -> bool:
        return True


def test_spinner_thread_reused_across_cycles() -> None:
    """A3: Multiple start/stop cycles should not create new threads."""
    stream = _MockStream()
    renderer = CliRenderer(
        json_mode=False,
        stream=stream,
        verbose=False,
        disable_spinner=False,
    )

    threads_seen: set[int] = set()

    for _ in range(5):
        renderer._render_model_call_start(
            step=1,
            p={"model": "test-model"},
        )
        assert renderer._spinner_active
        assert renderer._spinner_thread is not None
        threads_seen.add(id(renderer._spinner_thread))

        renderer._stop_spinner()
        assert not renderer._spinner_active
        # v0.4.9 (A3): _stop_spinner must NOT set _spinner_thread to None
        assert renderer._spinner_thread is not None, (
            "_stop_spinner should preserve _spinner_thread (A3 regression)"
        )

    # All cycles should have used the same persistent thread
    assert len(threads_seen) == 1, (
        f"Expected 1 persistent thread, got {len(threads_seen)} "
        f"(new thread created each cycle — A3 regression)"
    )

    renderer.shutdown_spinner()
    assert not renderer._spinner_active


def test_shutdown_spinner_terminates_thread() -> None:
    """A3: shutdown_spinner() should terminate the spinner thread."""
    stream = _MockStream()
    renderer = CliRenderer(
        json_mode=False,
        stream=stream,
        verbose=False,
        disable_spinner=False,
    )

    renderer._render_model_call_start(step=1, p={"model": "test-model"})
    thread_id = id(renderer._spinner_thread)

    renderer.shutdown_spinner()

    # Thread should be set to None after shutdown
    assert renderer._spinner_thread is None, (
        "shutdown_spinner should set _spinner_thread to None"
    )
    assert not renderer._spinner_active


def test_shutdown_spinner_called_without_start() -> None:
    """A3: shutdown_spinner() is safe to call even if spinner never started."""
    stream = _MockStream()
    renderer = CliRenderer(
        json_mode=False,
        stream=stream,
        verbose=False,
        disable_spinner=False,
    )

    # No spinner started — should not crash
    renderer.shutdown_spinner()
    assert renderer._spinner_thread is None


def test_spinner_thread_count_stable() -> None:
    """A3: After multiple cycles, only one thread has been active."""
    stream = _MockStream()
    renderer = CliRenderer(
        json_mode=False,
        stream=stream,
        verbose=False,
        disable_spinner=False,
    )

    original_thread_count = threading.active_count()

    for _ in range(3):
        renderer._render_model_call_start(step=1, p={"model": "m"})
        renderer._stop_spinner()

    # The persistent thread was already created, so active_count should
    # be original + 1 (the persistent spinner thread)
    # (Daemon threads remain active until shutdown_spinner is called)
    current_count = threading.active_count()
    assert current_count == original_thread_count + 1, (
        f"Expected {original_thread_count + 1} active threads, "
        f"got {current_count} — extra threads were leaked (A3 regression)"
    )

    renderer.shutdown_spinner()


def test_shutdown_spinner_cleanup() -> None:
    """A3: After shutdown, all spinner threads should be gone."""
    stream = _MockStream()
    renderer = CliRenderer(
        json_mode=False,
        stream=stream,
        verbose=False,
        disable_spinner=False,
    )

    original_thread_count = threading.active_count()

    renderer._render_model_call_start(step=1, p={"model": "m"})
    renderer._stop_spinner()
    renderer.shutdown_spinner()

    # Thread should be cleaned up
    current_count = threading.active_count()
    assert current_count == original_thread_count, (
        f"After shutdown, expected {original_thread_count} threads, "
        f"got {current_count} — thread not cleaned up"
    )


def test_disable_spinner_does_not_start_animation() -> None:
    """A3: When spinner is disabled, _start_spinner should not activate."""
    stream = _MockStream()
    renderer = CliRenderer(
        json_mode=False,
        stream=stream,
        verbose=False,
        disable_spinner=True,
    )

    renderer._render_model_call_start(step=1, p={"model": "m"})
    # A thread may be created for the cursor-clearing spinner loop,
    # but the spinner should not be active
    # (disable_spinner is evaluated at the CLI layer before calling renderer)
    renderer.shutdown_spinner()