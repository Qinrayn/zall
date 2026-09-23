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


@pytest.fixture(autouse=True)
def _isolate_always_allow(tmp_path_factory, monkeypatch):
    """E4.3: 隔离 always_allow.json, 防止测试污染真实 ~/.zall/always_allow.json。

    E4 引入了跨会话权限持久化 (CliUserResponder._persistent_allow 读写
    ~/.zall/always_allow.json)。若不隔离, 一个测试按 'a' 写入真实文件后,
    后续所有 CLI 测试的 greylist 工具会被自动 ACCEPT, 破坏测试独立性。
    本 fixture autouse, 把 always_allow_path 重定向到独立临时目录。

    用 tmp_path_factory (而非 tmp_path) 因为某些测试文件重定义了 tmp_path
    为 str, 会破坏 Path 拼接。
    """
    fake_path = tmp_path_factory.mktemp("always_allow") / "always_allow.json"
    monkeypatch.setattr(
        "zall.cli.responder._always_allow_path", lambda: fake_path
    )
    yield


@pytest.fixture(autouse=True)
def _freeze_live_windows(monkeypatch):
    """G7: 测试期间冻结 `_LIVE_WINDOWS` 写入, 防后台探测线程污染注入值。

    REPL / cmd_model 的 /models 探测 (REPL 启动 harvest、/model 菜单) 在
    **后台线程**跑, 本机有真 key 时会真探测并 set_live_windows(整体替换)。
    若某个后台线程恰好在"monkeypatch 注入 dict"的测试执行期间完成, 会把
    注入值清掉 → 全量跑挂 / 单文件过 (时序 flaky)。autouse 把更新入口
    (set_live_windows) 在每测期间替换为 no-op: 后台线程对真实表 write 被
    挡住, 测试用 monkeypatch.setattr(mr, "_LIVE_WINDOWS", …) 的显式注入
    仍生效。
    """
    import zall._util.model_registry as _mr

    monkeypatch.setattr(_mr, "_CUSTOM_WINDOWS", {})
    monkeypatch.setattr(_mr, "_CUSTOM_PRICES", {})
    monkeypatch.setattr(_mr, "_LIVE_WINDOWS", {})
    monkeypatch.setattr(_mr, "set_live_windows", lambda _m: None)
    # `_merge_custom_providers()` (经 cmd_model/cmd_provider 等路径触发) 会把
    # 真实 ~/.zall/config.toml 的 window_size/provider 注入上面三个全局表并
    # 残留 — 与 always_allow.json 同类的"真实机器配置泄漏进测试"问题, 必须
    # 每测前重置, 否则先跑过该路径的测试会污染后跑测试的 live/custom 表
    # (实测: 全量跑时 live_window 测试拿到 128000 而非注入的 1048576)。
    import zall.cli.model_switch as _msw

    monkeypatch.setattr(_msw, "harvest_live_windows", lambda *a, **k: 0)
    yield