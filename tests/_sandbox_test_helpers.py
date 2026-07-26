"""Fake plugin tool factories for subprocess sandbox tests.

These modules are imported by the subprocess runner during sandbox tests.
The tests/ directory is added to sys.path so the subprocess can find them.
"""

from __future__ import annotations

from zall.core.tool import ToolResult


class _FakeSimpleTool:
    """A simple tool that returns a fixed result."""

    def __init__(self) -> None:
        self.tool_id = "fake_simple"
        self.schema = {"type": "object", "properties": {}}
        self.execute_called = False

    def execute(self, args: dict) -> ToolResult:
        self.execute_called = True
        return ToolResult(
            success=True,
            output="hello from sandbox",
            artifacts={"called": True, "args": dict(args)},
        )


def fake_simple_factory() -> _FakeSimpleTool:
    """Factory for _FakeSimpleTool."""
    return _FakeSimpleTool()


class _FakeCrashingTool:
    """A tool that crashes during execution."""

    def __init__(self) -> None:
        self.tool_id = "fake_crash"
        self.schema = {"type": "object", "properties": {}}

    def execute(self, args: dict) -> ToolResult:
        msg = "intentional crash for sandbox test"
        raise RuntimeError(msg)


def fake_crash_factory() -> _FakeCrashingTool:
    """Factory for _FakeCrashingTool."""
    return _FakeCrashingTool()


class _FakeInfiniteLoopTool:
    """A tool that loops forever (for timeout testing)."""

    def __init__(self) -> None:
        self.tool_id = "fake_infinite"
        self.schema = {"type": "object", "properties": {}}

    def execute(self, args: dict) -> ToolResult:
        import time
        while True:
            time.sleep(1)


def fake_infinite_factory() -> _FakeInfiniteLoopTool:
    """Factory for _FakeInfiniteLoopTool."""
    return _FakeInfiniteLoopTool()


class _FakeComplexResultTool:
    """A tool that returns a complex ToolResult with rich artifacts."""

    def __init__(self) -> None:
        self.tool_id = "fake_complex"
        self.schema = {"type": "object", "properties": {}}

    def execute(self, args: dict) -> ToolResult:
        return ToolResult(
            success=True,
            output="complex result with artifacts",
            artifacts={
                "files": ["a.txt", "b.txt"],
                "count": 42,
                "nested": {"key": "value", "items": [1, 2, 3]},
            },
        )


def fake_complex_factory() -> _FakeComplexResultTool:
    """Factory for _FakeComplexResultTool."""
    return _FakeComplexResultTool()