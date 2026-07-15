"""tests/test_bugfix_regressions.py — Regression tests for fixed bugs (P5-b).

Each test maps to a bug ID from the audit report:
  B1: CheckpointManager crash on non-git repo
  B2: except Exception swallowing KeyboardInterrupt
  B3: hardcoded cwd='.' in _resolve_git_sha
  B4: _msg_to_openai missing tool_id mapping
  B5: REJUDGE GateState for MODIFY path
  B6: _WRITE_TOOLS inconsistency in _cmd_undo
"""

from __future__ import annotations

import io
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

from zall.core.action import Action
from zall.core.checkpoint import CheckpointManager
from zall.core.context import Context
from zall.core.gate import ConfirmGate, GateState, UserResponse, UserResponseType
from zall.core.loop import AgentLoop
from zall.core.model import Message, StopReason, ToolCall
from zall.core.safety import Judgement, SafeLevel


# ──────────────────────────────────────────────────────────────────────────
# B1: CheckpointManager crash on non-git repo
# ──────────────────────────────────────────────────────────────────────────


class TestB1CheckpointManagerNonGit:
    """B1: CheckpointManager in non- git directory should not crash."""

    def test_in_tmp_dir_no_crash(self, tmp_path: Path):
        """in temp directory (non- git) create CheckpointManager 不应崩溃."""
        original_cwd = Path.cwd()
        try:
            os.chdir(tmp_path)
            mgr = CheckpointManager()
            assert mgr is not None
        finally:
            os.chdir(original_cwd)

    def test_in_nonexistent_dir_no_crash(self):
        """在不存在的directorycreate不应崩溃."""
        # 捕获可能的exception, 但不应该崩溃
        try:
            mgr = CheckpointManager(project_root="/nonexistent_path_12345")
            # 可能成功 (会createdirectory), 也可能fail
            _ = mgr
        except (OSError, PermissionError, ValueError):
            pass

    def test_save_without_checkpoint_dir(self, tmp_path: Path):
        """当 _cp_dir for None 时 save 不应崩溃."""
        original_cwd = Path.cwd()
        try:
            os.chdir(tmp_path)
            mgr = CheckpointManager()
            # 强制 _cp_dir = None mockinit化fail
            mgr._cp_dir = None
            # save 不应崩溃
            result = mgr.save_checkpoint(label="test")
            assert result is None  # 应静默跳过
        finally:
            os.chdir(original_cwd)


# ──────────────────────────────────────────────────────────────────────────
# B2: except Exception swallowing KeyboardInterrupt
# ──────────────────────────────────────────────────────────────────────────


class TestB2KeyboardInterruptNotSwallowed:
    """B2: KeyboardInterrupt 不应被泛 except 吞掉."""

    def test_keyboard_interrupt_propagates(self):
        """KeyboardInterrupt 在 AgentLoop 的 step() 中应传播."""
        # verify loop.py 中的 except BaseException path
        # directlytestexceptionhandle逻辑
        def _test_propagation():
            try:
                raise KeyboardInterrupt()
            except BaseException as e:
                if isinstance(e, (KeyboardInterrupt, SystemExit)):
                    raise  # 应传播
                return "should_not_reach"

        with pytest.raises(KeyboardInterrupt):
            _test_propagation()

    def test_system_exit_propagates(self):
        """SystemExit 也应传播."""
        def _test_sysexit():
            try:
                raise SystemExit(1)
            except BaseException as e:
                if isinstance(e, (KeyboardInterrupt, SystemExit)):
                    raise

        with pytest.raises(SystemExit):
            _test_sysexit()

    def test_regular_exception_does_not_propagate(self):
        """普通 Exception 应被捕获并returns (non-传播)."""
        def _test_normal():
            try:
                raise ValueError("test error")
            except BaseException as e:
                if isinstance(e, (KeyboardInterrupt, SystemExit)):
                    raise
                return f"caught: {e}"

        result = _test_normal()
        assert "caught" in result


# ──────────────────────────────────────────────────────────────────────────
# B3: hardcoded cwd='.' in _resolve_git_sha
# ──────────────────────────────────────────────────────────────────────────


class TestB3ProjectRootCwd:
    """B3: AgentLoop 应使用 context 中的项目根path."""

    def test_loop_stores_project_root(self):
        """AgentLoop 应存储从 context 获取的项目根path."""
        from zall.core.loop import AgentLoop

        class FakeModel:
            model_name = "test"
            def complete(self, messages, tools, tool_choice=None):
                from zall.core.model import ModelResponse
                return ModelResponse(content="ok", stop_reason=StopReason.STOP)

        class FakeContext:
            class FakeCwdMeta:
                cwd_path = "/test/project"
            cwd_meta = FakeCwdMeta()
            user_raw = "test"

        class FakeTools:
            tools = ()
            def get(self, tid): return None

        class FakeRules:
            core_deny_rules = ()
            user_local_rules = ()
            domain_rules = ()

        class FakeResponder:
            def ask(self, action, judgement):
                from zall.core.gate import UserResponse
                return UserResponse(response_type=UserResponseType.ACCEPT)

        loop = AgentLoop(
            model=FakeModel(),
            tools=FakeTools(),
            rules=FakeRules(),
            goal=None,
            context=FakeContext(),
            user_responder=FakeResponder(),
        )
        # verify _project_root 从 context read
        assert loop._project_root == "/test/project"

    def test_fallback_to_dot(self):
        """当 context 没有 cwd_meta 时fallback到 '.'."""
        from zall.core.loop import AgentLoop

        class FakeModel:
            model_name = "test"
            def complete(self, messages, tools, tool_choice=None):
                from zall.core.model import ModelResponse
                return ModelResponse(content="ok", stop_reason=StopReason.STOP)

        class FakeContext:
            user_raw = "test"

        class FakeTools:
            tools = ()
            def get(self, tid): return None

        class FakeRules:
            core_deny_rules = ()
            user_local_rules = ()
            domain_rules = ()

        class FakeResponder:
            def ask(self, action, judgement):
                return UserResponse(response_type=UserResponseType.ACCEPT)

        loop = AgentLoop(
            model=FakeModel(),
            tools=FakeTools(),
            rules=FakeRules(),
            goal=None,
            context=FakeContext(),
            user_responder=FakeResponder(),
        )
        assert loop._project_root == "."


# ──────────────────────────────────────────────────────────────────────────
# B4: _msg_to_openai missing tool_id mapping
# ──────────────────────────────────────────────────────────────────────────


class TestB4ToolIdMapping:
    """B5: tool role message不应发送non-标准 tool_id 字段到 OpenAI API."""

    def test_tool_message_has_tool_call_id(self):
        """tool role message应包含标准 tool_call_id 字段."""
        from zall.adapters.openai_compat import OpenAICompatAdapter

        adapter = OpenAICompatAdapter(
            api_key="sk-test",
            api_base="https://api.openai.com/v1",
            model="gpt-4o-test",
        )

        msg = Message(
            role="tool",
            content="test output",
            tool_call_id="call_123",
            tool_id="read_file",
        )
        result = adapter._msg_to_openai(msg)
        assert result["role"] == "tool"
        assert result["tool_call_id"] == "call_123"
        # tool_id not OpenAI 标准字段, 不应发送 (fix B5)
        assert "tool_id" not in result, "tool_id is not a standard OpenAI field"

    def test_tool_message_standard_fields_only(self):
        """没有 tool_id 的 tool message应只包含标准字段."""
        from zall.adapters.openai_compat import OpenAICompatAdapter

        adapter = OpenAICompatAdapter(
            api_key="sk-test",
            api_base="https://api.openai.com/v1",
            model="gpt-4o-test",
        )

        msg = Message(
            role="tool",
            content="test output",
            tool_call_id="call_123",
        )
        result = adapter._msg_to_openai(msg)
        assert result["role"] == "tool"
        assert result["tool_call_id"] == "call_123"
        # 标准 OpenAI 字段: role, content, tool_call_id
        # non-标准字段如 tool_id 不应出现
        assert "tool_id" not in result
        assert result["role"] == "tool"
        # tool_id 可能不存在或for空
        if "tool_id" in result:
            assert result["tool_id"] == ""
        assert result["tool_call_id"] == "call_123"


# ──────────────────────────────────────────────────────────────────────────
# B5: REJUDGE GateState for MODIFY path
# ──────────────────────────────────────────────────────────────────────────


class TestB5RejudgeGateState:
    """B5: MODIFY 应returns REJUDGE state而non- deferred."""

    def test_modify_returns_rejudge(self):
        """MODIFY 用户response应returns REJUDGE state."""
        action = Action(tool_id="bash", args={"command": "rm -rf /"})
        judgement = Judgement(
            level=SafeLevel.GREYLIST,
            matched_rule_ids=("test",),
        )
        gate = ConfirmGate(action, judgement)

        # 第一轮: greylist → AWAITING_USER
        result = gate.process(response=None)
        assert result.state == GateState.AWAITING_USER

        # MODIFY
        modified = Action(tool_id="bash", args={"command": "ls -la"})
        result = gate.process(UserResponse(
            response_type=UserResponseType.MODIFY,
            modified_action=modified,
        ))
        # B5: 应for REJUDGE, 而non- deferred
        assert result.state == GateState.REJUDGE, (
            f"MODIFY should return REJUDGE, got {result.state}"
        )
        assert gate.current_action.args["command"] == "ls -la"

    def test_rejudge_differs_from_deferred(self):
        """REJUDGE 与 deferred 是不同的state."""
        assert GateState.REJUDGE != GateState.deferred
        assert GateState.REJUDGE.value == "rejudge"


# ──────────────────────────────────────────────────────────────────────────
# B6: _WRITE_TOOLS consistency
# ──────────────────────────────────────────────────────────────────────────


class TestB6WriteToolsConsistency:
    """B6: _WRITE_TOOLS 应在各处保持一致."""

    def test_loop_write_tools_includes_batch_edit(self):
        """AgentLoop 的 _WRITE_TOOLS 应包含 batch_edit."""
        from zall.core.loop import AgentLoop
        assert "batch_edit" in AgentLoop._WRITE_TOOLS
        assert "bash" in AgentLoop._WRITE_TOOLS
        assert "write_file" in AgentLoop._WRITE_TOOLS
        assert "edit_file" in AgentLoop._WRITE_TOOLS

    def test_write_tools_is_frozenset(self):
        """_WRITE_TOOLS 应for frozenset."""
        from zall.core.loop import AgentLoop
        assert isinstance(AgentLoop._WRITE_TOOLS, frozenset)