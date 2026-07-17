"""v0.0.13 interaction layer — §9.2.6 TodoWrite progress projection + §9.2.2 streaming assistant 前缀.

每个testincludes counterexamples (PR-1). 不破坏既有 render invariant (non- TTY 无 ANSI, 首 token 带 step).
"""

from __future__ import annotations

import io

from zall.core.loop_events import LoopEvent
from zall.cli.render import CliRenderer
from zall.tools.todo import TodoListTool
from zall.safety.rules_file import load_rules
from zall.core.safety import SafeLevel


def _ev(kind: str, step: int = 1, **payload: object) -> LoopEvent:
    return LoopEvent(kind=kind, step=step, payload=payload)


class _FakeTty(io.StringIO):
    """假装是 TTY 的 stream (isatty → True), 用于测 TTY 彩色渲染path."""

    def isatty(self) -> bool:  # noqa: D401
        return True


_SAMPLE_TODOS = [
    {"content": "read the spec", "status": "completed"},
    {"content": "write the parser", "status": "in_progress", "active_form": "Writing parser"},
    {"content": "add tests", "status": "pending"},
]


# ──────────────────────────────────────────────────────────────────────────
# TodoListTool 单元
# ──────────────────────────────────────────────────────────────────────────


class TestTodoTool:
    def test_valid_returns_todos_in_artifacts(self) -> None:
        """Happy path: valid todos → success + artifacts["todos"] 全量."""
        tool = TodoListTool()
        res = tool.execute({"todos": _SAMPLE_TODOS})
        assert res.success is True
        assert res.artifacts.get("todos") == _SAMPLE_TODOS
        assert res.artifacts.get("todo_event") is True

    def test_empty_list_rejected(self) -> None:
        """Counterexample: 空 list → success=False + non-空 error (不静默fail)."""
        tool = TodoListTool()
        res = tool.execute({"todos": []})
        assert res.success is False
        assert res.error  # non-空

    def test_missing_todos_rejected(self) -> None:
        """Counterexample: 缺 todos 字段 → success=False."""
        tool = TodoListTool()
        res = tool.execute({})
        assert res.success is False

    def test_bad_status_normalized_to_pending(self) -> None:
        """Counterexample: non-法 status → 规整for pending (does not crash)."""
        tool = TodoListTool()
        res = tool.execute({"todos": [{"content": "x", "status": "weird"}]})
        assert res.success is True
        assert res.artifacts["todos"][0]["status"] == "pending"

    def test_empty_content_rejected(self) -> None:
        """Counterexample: content for空 → success=False."""
        tool = TodoListTool()
        res = tool.execute({"todos": [{"content": "  ", "status": "pending"}]})
        assert res.success is False

    def test_count_bounded(self) -> None:
        """Counterexample: 超长清单 → truncate到 _MAX_TODOS (防撑爆渲染)."""
        tool = TodoListTool()
        many = [{"content": f"t{i}", "status": "pending"} for i in range(100)]
        res = tool.execute({"todos": many})
        assert res.success is True
        assert len(res.artifacts["todos"]) <= 30


# ──────────────────────────────────────────────────────────────────────────
# 渲染: TTY 面板
# ──────────────────────────────────────────────────────────────────────────


class TestTodoRenderTty:
    def test_panel_renders_checklist_and_footer(self) -> None:
        """Happy path: TTY 面板含 tasks title / 三项state / 'done' 注脚."""
        buf = _FakeTty()
        r = CliRenderer(stream=buf)
        r(_ev("tool_call_end", step=1, tool_id="todo_list", success=True,
              output="updated 3 todos", artifacts={"todos": _SAMPLE_TODOS}))
        out = buf.getvalue()
        assert "tasks" in out
        assert "\u25c9" in out and "\u25cc" in out and "\u25e6" in out
        assert "done" in out
        assert "1/3" in out  # done count
        # 不打印冗余的 tool digest行
        assert "updated" not in out

    def test_all_completed_still_shows_footer(self) -> None:
        """Counterexample (anti-cheat): 全 completed title显示 'done' 而non- 'met',
        明示"清单全打勾 ≠ met" (§9.2.6 偷渡风险)."""
        buf = _FakeTty()
        r = CliRenderer(stream=buf)
        todos = [{"content": "a", "status": "completed"},
                 {"content": "b", "status": "completed"}]
        r(_ev("tool_call_end", step=1, tool_id="todo_list", success=True,
              output="updated 2 todos", artifacts={"todos": todos}))
        out = buf.getvalue()
        assert "2/2" in out
        assert "done" in out
        assert "met" not in out

    def test_renderer_state_updated(self) -> None:
        """Happy path: 渲染后 self._todos 被刷新 (供跨 turn 持续显示)."""
        buf = _FakeTty()
        r = CliRenderer(stream=buf)
        r(_ev("tool_call_end", step=1, tool_id="todo_list", success=True,
              output="x", artifacts={"todos": _SAMPLE_TODOS}))
        assert r._todos == _SAMPLE_TODOS


# ──────────────────────────────────────────────────────────────────────────
# 渲染: non- TTY downgrade (无 ANSI)
# ──────────────────────────────────────────────────────────────────────────


class TestTodoRenderNonTty:
    def test_non_tty_no_ansi(self) -> None:
        """Counterexample: non- TTY output不含 ANSI 转义码."""
        buf = io.StringIO()
        r = CliRenderer(stream=buf)
        r(_ev("tool_call_end", step=1, tool_id="todo_list", success=True,
              output="x", artifacts={"todos": _SAMPLE_TODOS}))
        out = buf.getvalue()
        assert "\x1b[" not in out
        assert "tasks" in out
        assert "\u25c9" in out and "\u25e6" in out
        assert "done" in out

    def test_non_tty_no_redundant_summary(self) -> None:
        """Counterexample: non- TTY 同样不打冗余digest行."""
        buf = io.StringIO()
        r = CliRenderer(stream=buf)
        r(_ev("tool_call_end", step=1, tool_id="todo_list", success=True,
              output="updated 3 todos", artifacts={"todos": _SAMPLE_TODOS}))
        assert "updated" not in buf.getvalue()


# ──────────────────────────────────────────────────────────────────────────
# §9.2.2 streaming TTY: Claude Code 式无 assistant 前缀 (v0.0.16 去塑料感)
# ──────────────────────────────────────────────────────────────────────────


class TestStreamPrefix:
    def test_tty_first_token_has_no_assistant_prefix(self) -> None:
        """Happy path: TTY streaming首个 token 无 ✦ decorator前缀 (Claude Code 式, 无前缀符号).

        §9.2.2 原契约是带 ✦ 前缀; v0.0.16 去塑料感: 吸收 Claude Code —
        assistant 文本directlystreaming, 不加装饰星号 (tool/思考各用 ✻ 区分).
        Counterexample (IPR-0): ✦ 不得出现; 内容本体 (Hello) 仍正常显示.
        """
        buf = _FakeTty()
        r = CliRenderer(stream=buf, disable_spinner=True)
        # 尾空格触发节stream flush (TTY streaming按词边界刷新, non-逐字符)
        r(_ev("model_token", step=1, token="Hello "))
        out = buf.getvalue()
        assert "✦" not in out  # 无装饰前缀 (IPR-0 Counterexample)
        assert "Hello" in out  # 内容本体仍显示 (Happy path)

    def test_non_tty_first_token_unchanged(self) -> None:
        """Counterexample: non- TTY 首 token 仍走 'step N · ' 形态 (保旧invariant)."""
        buf = io.StringIO()
        r = CliRenderer(stream=buf, disable_spinner=True)
        r(_ev("model_token", step=1, token="Hello"))
        out = buf.getvalue()
        assert "step 1" in out
        assert "✦" not in out  # non- TTY 不加 ✦


# ──────────────────────────────────────────────────────────────────────────
# todo_list default whitelist (显示型免confirm)
# ──────────────────────────────────────────────────────────────────────────


class TestTodoWhitelist:
    def test_todo_list_whitelisted_by_default(self) -> None:
        """Happy path: defaultrule集含 native_allow_todo 且for whitelist (免confirm)."""
        rules = load_rules()
        match = [r for r in rules.user_local_rules if r.rule_id == "native_allow_todo"]
        assert match, "native_allow_todo 规则缺失"
        assert match[0].level == SafeLevel.WHITELIST

    def test_todo_list_whitelisted_even_with_custom_rules(self) -> None:
        """Counterexample: 即使传入自定义 rules, todo_list 仍 whitelist (代码层无条件injection)."""
        custom = load_rules(user_path="tests/fixtures/nonexistent_rules.toml")
        match = [r for r in custom.user_local_rules if r.rule_id == "native_allow_todo"]
        assert match and match[0].level == SafeLevel.WHITELIST
