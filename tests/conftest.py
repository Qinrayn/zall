"""pytest conftest — shared fixtures for zall CLI tests.

Shared helpers extracted from cross-file duplicates: _FakeLoop, _FakeTTY, _FakeModel, etc.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from zall.core.model import Message, ModelResponse, StopReason
from zall.core.verifiability import EventType, RunRecorder


# ── Fake helper classes ──


@dataclass
class _FakeEvent:
    """Minimal event struct (more efficient than dynamic type() creation)."""
    event_id: str
    ts: int
    event_type: EventType
    payload: dict[str, Any]


class _FakeTTY:
    """StringIO simulating isatty()=True (for render tests)."""

    def __init__(self) -> None:
        import io
        self._io = io.StringIO()

    def write(self, s: str) -> int:
        return self._io.write(s)

    def flush(self) -> None:
        pass

    def isatty(self) -> bool:
        return True

    def getvalue(self) -> str:
        return self._io.getvalue()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._io, name)


class _FakeModel:
    """Minimal model adapter returning preset responses."""

    model_name = "fake"

    def __init__(self, responses: list[ModelResponse] | None = None) -> None:
        self._responses = responses or []
        self._call_count = 0

    def complete(self, messages, tools, tool_choice) -> ModelResponse:
        if self._call_count < len(self._responses):
            resp = self._responses[self._call_count]
            self._call_count += 1
            return resp
        return ModelResponse(content="", stop_reason=StopReason.STOP)


class _FakeRecorder:
    """minimal recorder, 存储eventlist."""

    def __init__(self) -> None:
        self.events: list[_FakeEvent] = []

    def append(self, event_id, ts, event_type, payload) -> None:
        self.events.append(_FakeEvent(
            event_id=event_id, ts=ts,
            event_type=event_type, payload=payload,
        ))


class _FakeLoop:
    """Minimal AgentLoop mock for CLI command testing.

    Provides public API properties (messages, recorder, model_adapter, git_protect, etc.)
    and a private _messages fallback (compatible with legacy tests).
    """

    def __init__(self, messages: list | None = None) -> None:
        self._messages = messages or []
        self._recorder = _FakeRecorder()
        self._model = _FakeModel()
        self._git_protect = None
        self._checkpoint_mgr = None
        self._compactor = None
        self._step_count = 0
        self._plan_mode = False

    @property
    def messages(self) -> list:
        return list(self._messages)

    @property
    def recorder(self) -> _FakeRecorder:
        return self._recorder

    @property
    def model_adapter(self) -> _FakeModel:
        return self._model

    @property
    def git_protect(self) -> None:
        return self._git_protect

    @property
    def checkpoint_manager(self) -> None:
        return self._checkpoint_mgr

    @property
    def compactor(self) -> None:
        return self._compactor

    @property
    def step_count(self) -> int:
        return self._step_count

    @property
    def plan_mode(self) -> bool:
        return self._plan_mode

    def set_messages(self, msgs: list) -> None:
        self._messages = list(msgs)

    def set_plan_mode(self, enabled: bool) -> None:
        self._plan_mode = enabled

    def add_user_message(self, content: str) -> None:
        self._messages.append(Message.user(content))

    def add_user_file_message(self, content: str) -> None:
        self._messages.append(Message(role="user", content=content))

    def remove_messages_by_predicate(self, predicate) -> int:
        before = len(self._messages)
        self._messages = [m for m in self._messages if not predicate(m)]
        return before - len(self._messages)


# ── Pytest fixtures ──


@pytest.fixture
def fake_loop() -> _FakeLoop:
    """Returns an empty _FakeLoop instance."""
    return _FakeLoop()


@pytest.fixture
def fake_tty() -> _FakeTTY:
    """Returns an isatty()=True output stream."""
    return _FakeTTY()


@pytest.fixture
def fake_model() -> _FakeModel:
    """Returns a default _FakeModel instance."""
    return _FakeModel()