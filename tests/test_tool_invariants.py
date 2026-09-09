"""Tool + ToolRegistry invariant test (DESIGN.md §4.2 tool层).

IPR-0: each test must contain a counterexample.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from zall.core.tool import Tool, ToolRegistry, ToolResult


# ──────────────────────────────────────────────────────────────────────────
# helpers
# ──────────────────────────────────────────────────────────────────────────


class _FakeTool:
    """Tool stub, for testing Registry."""

    __test__ = False  # pytest 不收

    def __init__(self, tool_id: str = "fake_tool") -> None:
        self._tool_id = tool_id

    @property
    def tool_id(self) -> str:
        return self._tool_id

    @property
    def schema(self) -> dict:
        return {"type": "object", "properties": {}}

    def execute(self, args: dict) -> ToolResult:
        return ToolResult(success=True, output=f"fake execution of {self._tool_id}")


class _AnotherFakeTool:
    """第二个 Tool stub, 用于测重复 tool_id."""

    __test__ = False

    @property
    def tool_id(self) -> str:
        return "fake_tool"  # 故意与 _FakeTool 相同

    @property
    def schema(self) -> dict:
        return {}

    def execute(self, args: dict) -> ToolResult:
        return ToolResult(success=True, output="another")


# ──────────────────────────────────────────────────────────────────────────
# ToolResult invariants
# ──────────────────────────────────────────────────────────────────────────


class TestToolResultInvariants:
    """ToolResult invariant."""

    def test_happy_path(self) -> None:
        """Happy path: valid ToolResult constructable."""
        r = ToolResult(success=True, output="done")
        assert r.success is True
        assert r.output == "done"

    def test_frozen_immutable(self) -> None:
        """Counterexample: construct后改 success → must raise."""
        r = ToolResult(success=True, output="x")
        with pytest.raises(ValidationError):
            r.success = False  # type: ignore[misc]

    def test_no_tool_history_marker(self) -> None:
        """ToolResult 不携带 tool 历史 (§4.3 核心斩断呼应)."""
        assert ToolResult.__no_tool_history__() is True

    def test_error_field_optional(self) -> None:
        """Happy path: error 可for None (成功时) 或 str (fail时)."""
        ok = ToolResult(success=True, output="done")
        assert ok.error is None

        fail = ToolResult(success=False, output="", error="command not found")
        assert fail.error == "command not found"

    def test_artifacts_dict_known_open(self) -> None:
        """Known OPEN: artifacts dict 可变 (与 Action.args 同型, 不假装)."""
        r = ToolResult(success=True, output="x")
        assert isinstance(r.artifacts, dict)


# ──────────────────────────────────────────────────────────────────────────
# Tool Protocol invariants
# ──────────────────────────────────────────────────────────────────────────


class TestToolProtocolInvariants:
    """Tool Protocol invariant."""

    def test_fake_tool_is_tool(self) -> None:
        """Happy path: _FakeTool 满足 Tool Protocol."""
        assert isinstance(_FakeTool(), Tool)

    def test_bad_object_not_tool(self) -> None:
        """Counterexample: 缺 execute 的对象not Tool."""

        class _Bad:
            @property
            def tool_id(self) -> str:
                return "x"

            @property
            def schema(self) -> dict:
                return {}

        assert not isinstance(_Bad(), Tool)

    def test_tool_id_non_empty(self) -> None:
        """Counterexample: tool_id for空 → 无意义 (与 Action.tool_id 同型约束).

        注: Protocol 不强制non-空 (Protocol 只查属性存在), 但实现应保证.
        本testverify _FakeTool 的 tool_id non-空作for示范.
        """
        tool = _FakeTool("bash")
        assert tool.tool_id != ""


# ──────────────────────────────────────────────────────────────────────────
# ToolRegistry invariants
# ──────────────────────────────────────────────────────────────────────────


class TestToolRegistryInvariants:
    """ToolRegistry invariant."""

    def test_happy_path(self) -> None:
        """Happy path: valid ToolRegistry constructable."""
        reg = ToolRegistry(tools=(_FakeTool("bash"), _FakeTool("read_file")))
        assert len(reg.tools) == 2

    def test_duplicate_tool_id_raises(self) -> None:
        """Counterexample: 两个 tool_id="bash" → must raise (find歧义).

        如果一个实现允许重复, get() returns哪个 Tool 是不确定的 →
        context_judge 判了 SafeLevel 但执行时拿到错误的 Tool.
        """
        with pytest.raises(ValidationError, match="duplicate tool_id"):
            ToolRegistry(tools=(_FakeTool("bash"), _FakeTool("bash")))

    def test_get_existing_tool(self) -> None:
        """Happy path: get 已register的 tool_id → returns Tool."""
        reg = ToolRegistry(tools=(_FakeTool("bash"),))
        tool = reg.get("bash")
        assert tool is not None
        assert tool.tool_id == "bash"

    def test_get_nonexistent_returns_none(self) -> None:
        """Happy path: get 未register的 tool_id → returns None (不 raise)."""
        reg = ToolRegistry(tools=(_FakeTool("bash"),))
        assert reg.get("nonexistent") is None

    def test_has_check(self) -> None:
        """Happy path: has correctlyreturns True/False."""
        reg = ToolRegistry(tools=(_FakeTool("bash"),))
        assert reg.has("bash") is True
        assert reg.has("nonexistent") is False

    def test_tool_ids_property(self) -> None:
        """Happy path: tool_ids returns所有已register id."""
        reg = ToolRegistry(tools=(_FakeTool("bash"), _FakeTool("read_file")))
        assert set(reg.tool_ids) == {"bash", "read_file"}

    def test_frozen_immutable(self) -> None:
        """Counterexample: construct后改 tools → must raise."""
        reg = ToolRegistry(tools=(_FakeTool("bash"),))
        with pytest.raises(ValidationError):
            reg.tools = (_FakeTool("read_file"),)  # type: ignore[misc]

    def test_empty_registry_ok(self) -> None:
        """Happy path: 空 Registry constructable (agent 启动时可能无tool)."""
        reg = ToolRegistry()
        assert len(reg.tools) == 0
        assert reg.tool_ids == ()


# ──────────────────────────────────────────────────────────────────────
# I-CTX-CAP: 上下文入口中心截断 (executor.clip_tool_output_for_context)
# 背景: 首轮对话就占 20% 上下文的真实反馈 — 工具输出进消息历史必须有上限。
# ──────────────────────────────────────────────────────────────────────


class TestContextCapInvariants:
    """I-CTX-CAP: 工具输出进入模型上下文前的中心截断。"""

    def test_small_output_passthrough(self) -> None:
        """Happy path: 未超限输出原样返回 (无副作用)。"""
        from zall.core.executor import clip_tool_output_for_context
        s = "line\n" * 100
        assert clip_tool_output_for_context(s) is s

    def test_oversize_output_clipped_with_notice(self) -> None:
        """超限输出: 长度受控 + 保头保尾 + 可见截断提示。"""
        from zall.core.executor import (
            MAX_CONTEXT_TOOL_OUTPUT,
            clip_tool_output_for_context,
        )
        s = "H" * 40_000 + "MIDDLE" + "T" * 40_000
        out = clip_tool_output_for_context(s)
        assert len(out) < MAX_CONTEXT_TOOL_OUTPUT + 500  # 提示长度容差
        assert out.startswith("H")          # 头部保留
        assert out.endswith("T" * 100)      # 尾部保留
        assert "context cap" in out         # 模型可感知截断
        assert "MIDDLE" not in out          # 反例: 中段确实被省略

    def test_executor_appends_clipped_tool_result(self) -> None:
        """集成面: 超大工具输出追加进消息历史时已被截断。

        反例孪生: 若 executor 绕过 clip (回归到直接 append 全量),
        消息长度断言必然失败。
        """
        from zall.core.context import Context
        from zall.core.executor import MAX_CONTEXT_TOOL_OUTPUT
        from zall.core.gate import UserResponse, UserResponseType
        from zall.core.goal import (
            AcceptanceContract,
            GoalStatement,
            GoalTriple,
            GoalType,
            TerminationState,
        )
        from zall.core.loop import AgentLoop
        from zall.core.model import ModelResponse, StopReason, ToolCall
        from zall.core.safety import RuleSet

        big = "X" * (MAX_CONTEXT_TOOL_OUTPUT * 3)

        class _BigTool:
            __test__ = False
            tool_id = "big_tool"
            schema = {"type": "function",
                      "function": {"name": "big_tool", "parameters": {
                          "type": "object", "properties": {}}}}

            def execute(self, args: dict) -> ToolResult:
                return ToolResult(success=True, output=big)

        class _Adapter:
            __test__ = False
            model_name = "fake"
            calls = 0

            def complete(self, messages, tools, tool_choice=None):
                self.calls += 1
                if self.calls == 1:
                    return ModelResponse(
                        content="", stop_reason=StopReason.TOOL_USE,
                        tool_calls=(ToolCall(id="c1", tool_id="big_tool", args={}),),
                    )
                return ModelResponse(content="done", stop_reason=StopReason.STOP)

        class _Accept:
            __test__ = False

            def ask(self, action, judgement):
                return UserResponse(response_type=UserResponseType.ACCEPT)

        class _UserTermination:
            exposed_dependency_set = None

            def __call__(self, state: object) -> TerminationState:
                return TerminationState.UNDECIDABLE

        class _Cwd:
            cwd_path = "/tmp/p"
            git_branch = "main"
            git_remote = "origin"

        goal = GoalTriple(
            statement=GoalStatement(
                intent="t", rewriting="t", rewrite_confidence=0.9,
                goal_type=GoalType.DOCS, translation_of=("seg1",),
                added_intent=(),
            ),
            termination=_UserTermination(),
            acceptance=AcceptanceContract(baseline_frozen_at="abc123"),
        )
        loop = AgentLoop(
            model=_Adapter(),
            tools=ToolRegistry(tools=(_BigTool(),)),
            rules=RuleSet(),
            goal=goal,
            context=Context(user_raw="t", cwd_meta=_Cwd()),
            user_responder=_Accept(),
        )
        loop.run(system_prompt="s")
        tool_msgs = [m for m in loop.messages if m.role == "tool"]
        assert tool_msgs, "tool result message missing"
        assert len(tool_msgs[0].content) < MAX_CONTEXT_TOOL_OUTPUT + 800
        assert "context cap" in tool_msgs[0].content

    def test_oversize_output_spilled_to_disk_with_paging_hint(self, tmp_path, monkeypatch) -> None:
        """kimi Background 输出协议对标 (I-CTX-SPILL): 超限输出全量落盘,
        截断提示附精确路径 + read_file 分页指引。

        反例孪生: 未超限输出不落盘 (目录不产生杂物)。
        """
        from types import SimpleNamespace

        from zall.core.executor import (
            MAX_CONTEXT_TOOL_OUTPUT,
            clip_with_spill,
        )
        loop = SimpleNamespace(
            _context=SimpleNamespace(cwd_meta=SimpleNamespace(cwd_path=str(tmp_path))),
            _step_count=2, _tool_call_count=7,
        )
        big = "Z" * (MAX_CONTEXT_TOOL_OUTPUT * 2)
        out = clip_with_spill(loop, "bash", big)
        assert "full output saved to:" in out
        assert "read_file" in out
        spill_dir = tmp_path / ".zall" / "tool_outputs"
        files = list(spill_dir.glob("*.txt"))
        assert len(files) == 1
        assert files[0].read_text(encoding="utf-8") == big   # 全量无损
        # 反例: 小输出原样返回且不新增落盘文件
        small = "ok"
        assert clip_with_spill(loop, "bash", small) is small
        assert len(list(spill_dir.glob("*.txt"))) == 1
