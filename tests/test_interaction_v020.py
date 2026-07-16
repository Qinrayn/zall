"""v0.0.20 interaction layer增强 — implementation tests (out-of-the-box / model switching / command prompt).

covers纯逻辑 (无real IO / 模型):
  1. _resolve_model_alias: 别名展开 / 大小写 / 未知原样
  2. _suggest_command: did-you-mean 拼错建议 / 无匹配 None
  3. _config_status: ready / placeholder / 空 key
  4. _cmd_model: 带参设置 / non- TTY 用法prompt / picker 编号选择 / picker 输名 / 空输入不变
  5. _onboarding: 已配置零干扰 / non- TTY 打指引 (patch ensure_config 防真副作用)
"""

from __future__ import annotations

import io
from typing import Any

import pytest

from zall.cli import app as app_mod
from zall.cli import config as config_mod
from zall.cli.app import (
    _config_status,
    _onboarding,
    _resolve_model_alias,
    _setup_completion,
    _suggest_command,
)
from zall.cli.commands.model import cmd_model


class _FakeTTY(io.StringIO):
    """StringIO 但 isatty()=True (mock终端, 触发交互branch)."""

    def isatty(self) -> bool:  # type: ignore[override]
        return True


# ──────────────────────────────────────────────────────────────────────────
# 1. _resolve_model_alias
# ──────────────────────────────────────────────────────────────────────────


class TestResolveAlias:
    def test_known_alias(self) -> None:
        assert _resolve_model_alias("flash") == "agnes-2.0-flash"
        assert _resolve_model_alias("mini") == "gpt-4o-mini"
        assert _resolve_model_alias("sonnet") == "claude-3-5-sonnet"

    def test_case_insensitive(self) -> None:
        assert _resolve_model_alias("FLASH") == "agnes-2.0-flash"
        assert _resolve_model_alias("Qwen") == "qwen-plus"

    def test_unknown_passthrough(self) -> None:
        assert _resolve_model_alias("gpt-4o-mini") == "gpt-4o-mini"
        assert _resolve_model_alias("some-custom-model") == "some-custom-model"

    def test_strips_whitespace(self) -> None:
        assert _resolve_model_alias("  flash  ") == "agnes-2.0-flash"


# ──────────────────────────────────────────────────────────────────────────
# 2. _suggest_command (did-you-mean)
# ──────────────────────────────────────────────────────────────────────────


class TestSuggestCommand:
    def test_typo_suggests_closest(self) -> None:
        assert _suggest_command("/modle") == "model"
        assert _suggest_command("/hel") == "help"

    def test_quit_typo(self) -> None:
        assert _suggest_command("/quitt") == "quit"

    def test_no_match_returns_none(self) -> None:
        assert _suggest_command("/zzzzzz") is None
        assert _suggest_command("/xyz") is None

    def test_strips_leading_slash(self) -> None:
        # 不带斜杠也能工作
        assert _suggest_command("modle") == "model"


# ──────────────────────────────────────────────────────────────────────────
# 3. _config_status
# ──────────────────────────────────────────────────────────────────────────


def _patch_config(monkeypatch: pytest.MonkeyPatch, **overrides: Any) -> None:
    base = {"api_key": "sk-test", "model": "agnes-1.5-flash",
            "api_base": "https://x/v1"}
    base.update(overrides)

    def fake_load() -> dict:
        return dict(base)

    monkeypatch.setattr("zall.safety.config.load_config", fake_load)


class TestConfigStatus:
    def test_ready_when_key_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_config(monkeypatch, api_key="sk-real")
        st = _config_status()
        assert st["ready"] is True
        assert st["api_key"] == "sk-real"

    def test_not_ready_when_placeholder(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_config(monkeypatch, api_key="your-api-key-here")
        assert _config_status()["ready"] is False

    def test_not_ready_when_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_config(monkeypatch, api_key="")
        assert _config_status()["ready"] is False


# ──────────────────────────────────────────────────────────────────────────
# 4. _cmd_model
# ──────────────────────────────────────────────────────────────────────────


class TestCmdModel:
    def test_with_arg_sets_state_alias(self) -> None:
        state: dict = {}
        out = io.StringIO()
        cmd_model("flash", out, None, state)
        assert state["model"] == "agnes-2.0-flash"
        assert "agnes-2.0-flash" in out.getvalue()

    def test_with_arg_unknown_asis(self) -> None:
        state: dict = {}
        out = io.StringIO()
        cmd_model("my-custom-model", out, None, state)
        assert state["model"] == "my-custom-model"

    def test_no_arg_non_tty_shows_usage(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_config(monkeypatch, model="agnes-2.0-flash")
        state: dict = {}
        out = io.StringIO()  # isatty False
        state["_input_fn"] = None
        cmd_model("", out, None, state)
        assert "current model" in out.getvalue()
        assert "usage: /model" in out.getvalue()
        assert "model" not in state  # 未设置

    def test_picker_select_by_number(self) -> None:
        state: dict = {}
        out = _FakeTTY()
        state["_input_fn"] = lambda _p: "1"
        cmd_model("", out, None, state)
        # First item in sorted list (by provider group 0, then by alias): agnes-1.5-flash
        assert state["model"] == "agnes-1.5-flash"

    def test_picker_type_name(self) -> None:
        state: dict = {}
        out = _FakeTTY()
        state["_input_fn"] = lambda _p: "deepseek"
        cmd_model("", out, None, state)
        assert state["model"] == "deepseek-chat"

    def test_picker_empty_input_no_change(self) -> None:
        state: dict = {"model": "keep-me"}
        out = _FakeTTY()
        state["_input_fn"] = lambda _p: ""
        cmd_model("", out, None, state)
        assert state["model"] == "keep-me"

    def test_picker_rejects_non_model_input(self) -> None:
        """Counterexample: picker input明显non-model名 (eg. 中文/逗号) → warning并preserve当前, 不设垃圾名."""
        state: dict = {"model": "keep-me"}
        out = _FakeTTY()
        state["_input_fn"] = lambda _p: "继续，"
        cmd_model("", out, None, state)
        assert state["model"] == "keep-me"  # 未改
        # New smart mode: fuzzy search shows "no match" message instead of "invalid chars"
        assert "no match" in out.getvalue() or "model name contains invalid characters" in out.getvalue()

    def test_picker_accepts_custom_alnum_model(self) -> None:
        """Happy path: picker input自定义model名 (字母数字) → accept (用户 api_base 支持)."""
        state: dict = {}
        out = _FakeTTY()
        state["_input_fn"] = lambda _p: "my-custom-Model_42"
        cmd_model("", out, None, state)
        assert state["model"] == "my-custom-Model_42"


# ──────────────────────────────────────────────────────────────────────────
# 5. _onboarding
# ──────────────────────────────────────────────────────────────────────────


class TestOnboarding:
    def test_ready_is_noop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_config(monkeypatch, api_key="sk-real")
        out = io.StringIO()
        _onboarding(out, input_fn=lambda _p: "should-not-ask")
        assert out.getvalue() == ""  # 已配置 → 零干扰, 不打印

    def test_non_tty_prints_guidance(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_config(monkeypatch, api_key="your-api-key-here")
        monkeypatch.setattr("zall.safety.config.ensure_config", lambda: None)
        out = io.StringIO()  # non- TTY
        _onboarding(out, input_fn=lambda _p: "x")
        assert "no API key" in out.getvalue()

    def test_tty_saves_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_config(monkeypatch, api_key="your-api-key-here")
        monkeypatch.setattr("zall.safety.config.ensure_config", lambda: None)
        saved: dict = {}

        def fake_save(key: str) -> None:
            saved["key"] = key

        monkeypatch.setattr("zall.safety.config.save_api_key", fake_save)
        out = _FakeTTY()
        _onboarding(out, input_fn=lambda _p: "sk-entered")
        assert saved.get("key") == "sk-entered"
        assert "saved" in out.getvalue()

    def test_tty_skip_does_not_save(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_config(monkeypatch, api_key="")
        monkeypatch.setattr("zall.safety.config.ensure_config", lambda: None)
        monkeypatch.setattr(
            "zall.safety.config.save_api_key",
            lambda key: pytest.fail("should not save on skip"),
        )
        out = _FakeTTY()
        _onboarding(out, input_fn=lambda _p: "")  # Enter 跳过
        assert "skipped" in out.getvalue()


# ──────────────────────────────────────────────────────────────────────────
# 6. _setup_completion (does not crash溃即可; Windows 无 readline 时静默skip)
# ──────────────────────────────────────────────────────────────────────────


def test_setup_completion_does_not_crash() -> None:
    from zall.skills import Skill

    _setup_completion([Skill(name="review", description="", prompt="r")])
    # 无 assert: 平台无 readline 时静默skip, 有则register补全; 只保证does not raise


# ──────────────────────────────────────────────────────────────────────────
# 7. /compact 不丢对话态 (Bug C 回归)
# ──────────────────────────────────────────────────────────────────────────


class _FakeModel:
    """minimal ModelAdapter (供 compactor 调 complete 生成digest)."""

    @property
    def model_name(self) -> str:
        return "fake"

    def complete(self, messages, tools, tool_choice=None):
        from zall.core.model import ModelResponse, StopReason

        return ModelResponse(
            content="Summary: developer asked to read files; agent did so.",
            stop_reason=StopReason.STOP,
        )


class _FakeLoop:
    """minimal loop 占位 (供 _cmd_compact 读 _messages/_model/_recorder/_step_count)."""

    def __init__(self, n_msgs: int, model=None):
        from zall.core.model import Message

        msgs = [Message(role="system", content="system prompt")]
        for i in range(n_msgs - 1):
            msgs.append(Message.user(f"u{i}") if i % 2 == 0
                        else Message.assistant(f"a{i}"))
        self._messages = msgs
        self._model = model
        self._recorder = None
        self._step_count = n_msgs


class TestCompactPreservesConversation:
    def test_compact_no_clear_when_no_model(self) -> None:
        """Counterexample (Bug C): /compact 无 model 时不得 return "clear" 丢弃对话态."""
        from zall.cli.app import _handle_slash

        loop = _FakeLoop(n_msgs=5, model=None)
        out = io.StringIO()
        result = _handle_slash("/compact", {}, out, loop=loop)
        assert result == "handled"  # 旧 bug 是 "clear"
        assert "no model" in out.getvalue()
        assert len(loop._messages) == 5  # 对话态保留

    def test_compact_no_clear_when_nothing_to_compact(self) -> None:
        """Counterexample (Bug C): /compact 无可压缩 (<=6 条) 时不得 "clear"."""
        from zall.cli.app import _handle_slash

        loop = _FakeLoop(n_msgs=5, model=_FakeModel())  # 5 <= 6 → 无需压缩
        out = io.StringIO()
        result = _handle_slash("/compact", {}, out, loop=loop)
        assert result == "handled"
        assert "nothing to compact" in out.getvalue()
        assert len(loop._messages) == 5  # 未改

    def test_compact_success_keeps_loop_replaces_messages(self) -> None:
        """Happy path (Bug C): /compact 成功时原地replace _messages 且 return "handled"
        (不 "clear" 丢弃压缩后的上下文)."""
        from zall.cli.app import _handle_slash

        loop = _FakeLoop(n_msgs=8, model=_FakeModel())  # 8 > 6 → 真压缩
        out = io.StringIO()
        result = _handle_slash("/compact", {}, out, loop=loop)
        assert result == "handled"  # 旧 bug 是 "clear" → 丢失压缩后上下文
        assert "compacted" in out.getvalue()
        # message被压缩 (8 → system + summary + 4 recent = 6), 且仍preserve在 loop 上
        assert len(loop._messages) < 8
        assert len(loop._messages) >= 1


# ──────────────────────────────────────────────────────────────────────────
# 8. Goal confirm用 input_fn (Bug D 回归: repl 须把 input_fn 传给 _confirm_goal)
# ──────────────────────────────────────────────────────────────────────────


class _StdinFake:
    """sys.stdin 占位: isatty()=True (强制 _confirm_goal 走promptpath)."""

    def isatty(self) -> bool:
        return True


def _make_input(lines):
    it = iter(lines)

    def _fn(_p="> "):
        try:
            return next(it)
        except StopIteration:
            raise EOFError

    return _fn


class TestGoalConfirmUsesInputFn:
    """Bug D: repl 的 Goal confirmmust用injection的 input_fn (旧implementation调real input())."""

    def _setup(self, monkeypatch: pytest.MonkeyPatch):
        # isolatereal副作用: onboarding / mcp / skills / readline 补全
        monkeypatch.setattr(app_mod, "_onboarding", lambda out, fn: None)
        monkeypatch.setattr(app_mod, "_build_mcp_tools", lambda *a, **k: [])
        monkeypatch.setattr(app_mod, "load_skills", lambda *a, **k: [])
        monkeypatch.setattr(app_mod, "_setup_completion", lambda *a, **k: None)
        # 强制 stdin TTY → 触发 _confirm_goal promptpath (否则non- TTY 自动confirm)
        monkeypatch.setattr("sys.stdin", _StdinFake())
        # fake adapter + 调用计数
        from zall.core.model import ModelResponse, StopReason
        calls = {"n": 0}

        class _Ad:
            def __init__(self, model=None, **kw):
                self._m = model or "fake"

            @property
            def model_name(self):
                return self._m

            def complete(self, messages, tools, tool_choice=None):
                calls["n"] += 1
                return ModelResponse(
                    content="REPLY", stop_reason=StopReason.STOP,
                    usage={"prompt": 1, "completion": 1, "total": 2},
                )

            def complete_stream(self, messages, tools, tool_choice=None):
                calls["n"] += 1
                yield ("REPLY", ModelResponse(
                    content="REPLY", stop_reason=StopReason.STOP,
                    usage={"prompt": 1, "completion": 1, "total": 2}))

        monkeypatch.setattr(config_mod, "_build_adapter", lambda provider=None, model=None, **kwargs: _Ad(model=model))
        return calls

    def test_reject_does_not_call_model(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Counterexample (Bug D): confirm input n → reject, 不得调model."""
        calls = self._setup(monkeypatch)
        out = _FakeTTY()
        app_mod.repl(input_fn=_make_input(["task", "n", "/exit"]),
                     out=out, stream=False, yes=False)
        assert "goal not confirmed" in out.getvalue()
        assert calls["n"] == 0  # 拒绝 → 不调模型

    def test_accept_calls_model(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Happy path (Bug D): confirm input y → accept, 调model (用injection的 input_fn)."""
        calls = self._setup(monkeypatch)
        out = _FakeTTY()
        app_mod.repl(input_fn=_make_input(["task", "y", "/exit"]),
                     out=out, stream=False, yes=False)
        assert "REPLY" in out.getvalue()
        assert calls["n"] >= 1  # 接受 → 调模型


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
