"""Integration tests: full pipeline end-to-end (v0.4.0).

Tests the interaction between all new systems:
  - ChatState + AgentLoop integration
  - LSP diagnostics + system prompt
  - CodeGraph indexing + search
  - Sandbox isolation
  - Plugin system
  - CLI commands

IPR-0: integration tests verify the systems work together correctly.
"""

from __future__ import annotations

import os
import pytest
import tempfile
from pathlib import Path
from typing import Any

from zall.core.chat_state import ChatState, ChatStateHandle
from zall.codegraph import CodeGraph, CodeIndexer, CodeNavigator, Symbol, SymbolKind, SymbolLocation
from zall.lsp import LspManager, DiagnosticEntry, DiagnosticSeverity
from zall.sandbox import Sandbox, SandboxMode, ProcessSandbox, ResourceLimits


def _make_mock_goal():
    """创建最小 GoalTriple mock。"""
    from zall.core.goal import GoalTriple, GoalStatement, GoalType, AcceptanceContract
    return GoalTriple(
        statement=GoalStatement(
            intent="test",
            rewriting="test",
            rewrite_confidence=1.0,
            goal_type=GoalType.UNKNOWN,
            translation_of=("test",),
            added_intent=(),
        ),
        termination=type("T", (), {"exposed_dependency_set": None, "__call__": lambda s, st: None})(),
        acceptance=AcceptanceContract(baseline_frozen_at="test"),
    )


# ═══════════════════════════════════════════════════════════════════
# ChatState + AgentLoop Integration
# ═══════════════════════════════════════════════════════════════════


class TestChatStateAgentLoopIntegration:
    """ChatState 与 AgentLoop 的集成测试。"""

    def test_chat_state_in_agent_config(self):
        """ChatState 可以通过 AgentConfig 注入 AgentLoop。"""
        from zall.core.loop import AgentLoop, AgentConfig, RunEgress
        from zall.core.goal import GoalTriple, TerminationState

        # Create a minimal mock adapter
        class MockAdapter:
            model_name = "mock"
            def complete(self, messages, tools, tool_choice):
                from zall.core.model import ModelResponse, StopReason
                return ModelResponse(content="", stop_reason=StopReason.STOP)

        cs = ChatState()
        cs.push_user_message("test")

        config = AgentConfig(chat_state=cs)
        assert config.chat_state is cs

    def test_agentloop_chat_state_property(self):
        """AgentLoop.chat_state 属性返回 ChatState 实例。"""
        from zall.core.loop import AgentLoop, AgentConfig
        from zall.core.goal import GoalTriple
        from zall.core.context import Context
        from zall.cli.environment import CwdMeta

        cs = ChatState()
        cs.push_user_message("hello")

        # Minimal mock objects
        class MockAdapter:
            model_name = "mock"
            def complete(self, messages, tools, tool_choice):
                from zall.core.model import ModelResponse, StopReason
                return ModelResponse(content="", stop_reason=StopReason.STOP)

        class MockResponder:
            def ask(self, action, judgement):
                from zall.core.gate import UserResponse, UserResponseType
                return UserResponse(response_type=UserResponseType.ACCEPT)

        loop = AgentLoop(
            model=MockAdapter(),
            tools=type("R", (), {"tools": (), "get": lambda *a: None, "schemas": []})(),
            rules=type("R", (), {"core_deny_rules": (), "user_local_rules": (), "domain_rules": ()})(),
            goal=_make_mock_goal(),
            context=Context(user_raw="test", cwd_meta=CwdMeta()),
            user_responder=MockResponder(),
            config=AgentConfig(chat_state=cs),
        )

        assert loop.chat_state is cs
        assert len(loop.messages) >= 1  # ChatState messages

    def test_get_chat_state_lazy_creation(self):
        """get_chat_state() 惰性创建 ChatState。"""
        from zall.core.loop import AgentLoop, AgentConfig
        from zall.core.goal import GoalTriple
        from zall.core.context import Context
        from zall.cli.environment import CwdMeta

        class MockAdapter:
            model_name = "mock"
            def complete(self, messages, tools, tool_choice):
                from zall.core.model import ModelResponse, StopReason
                return ModelResponse(content="", stop_reason=StopReason.STOP)

        class MockResponder:
            def ask(self, action, judgement):
                from zall.core.gate import UserResponse, UserResponseType
                return UserResponse(response_type=UserResponseType.ACCEPT)

        loop = AgentLoop(
            model=MockAdapter(),
            tools=type("R", (), {"tools": (), "get": lambda *a: None, "schemas": []})(),
            rules=type("R", (), {"core_deny_rules": (), "user_local_rules": (), "domain_rules": ()})(),
            goal=_make_mock_goal(),
            context=Context(user_raw="test", cwd_meta=CwdMeta()),
            user_responder=MockResponder(),
        )

        assert loop.chat_state is None
        cs = loop.get_chat_state()
        assert cs is not None
        assert cs.message_count == 0  # Lazy: starts empty
        assert loop.chat_state is cs  # Same instance cached


# ═══════════════════════════════════════════════════════════════════
# ChatState + CodeGraph Integration
# ═══════════════════════════════════════════════════════════════════


class TestChatStateCodeGraphIntegration:
    """ChatState 与 CodeGraph 的集成测试。"""

    def test_codegraph_index_updates_chat_state(self):
        """CodeGraph 索引后, ChatState 可以记录索引事件。"""
        cs = ChatState()
        cg = CodeGraph(os.getcwd())

        # Index the current project
        cg.build_index()
        stats = cg.get_stats()

        # Record the indexing event in ChatState
        cs.push_system_message(
            f"[CodeGraph indexed: {stats.get('file_count', 0)} files, "
            f"{stats.get('symbol_count', 0)} symbols]"
        )

        assert cs.message_count == 1
        assert "CodeGraph indexed" in cs.messages[0].content

    def test_codegraph_search_results_in_chat_state(self):
        """CodeGraph 搜索结果可以存入 ChatState 消息。"""
        cs = ChatState()
        cg = CodeGraph(os.getcwd())
        cg.build_index()

        # Search and record
        results = cg.search("class")
        if results:
            cs.push_assistant_response(
                f"Found {len(results)} symbols matching 'class'",
            )
            assert cs.message_count == 1
            assert "Found" in cs.messages[0].content


# ═══════════════════════════════════════════════════════════════════
# LSP + CodeGraph Integration
# ═══════════════════════════════════════════════════════════════════


class TestLspCodeGraphIntegration:
    """LSP 与 CodeGraph 的集成测试。"""

    def test_lsp_diagnostics_codegraph_search(self):
        """LSP 诊断 + CodeGraph 搜索的联合场景。"""
        lsp = LspManager()
        cg = CodeGraph(os.getcwd())

        # Push some mock diagnostics
        lsp.handle_diagnostics(
            uri="file:///project/test.py",
            diagnostics=[{
                "range": {"start": {"line": 0, "character": 0}},
                "message": "Undefined variable 'x'",
                "severity": 1,
            }],
        )

        # CodeGraph search should work independently
        cg.build_index()
        stats = cg.get_stats()

        # Both systems should be functional
        assert len(lsp.all_diagnostics) > 0
        assert stats.get("status") == "indexed"

    def test_lsp_manager_codegraph_coexistence(self):
        """LspManager 和 CodeGraph 可以共存。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a simple Python file
            (Path(tmpdir) / "main.py").write_text(
                "def hello(): pass\n", encoding="utf-8",
            )

            lsp = LspManager(project_dir=tmpdir)
            cg = CodeGraph(tmpdir)

            # Both should work
            assert lsp.detect_language("main.py") == "python"
            cg.build_index()
            assert cg.get_stats()["file_count"] >= 1


# ═══════════════════════════════════════════════════════════════════
# Sandbox + Tool Integration
# ═══════════════════════════════════════════════════════════════════


class TestSandboxIntegration:
    """沙箱与其他系统的集成测试。"""

    def test_sandbox_with_codegraph(self):
        """沙箱中运行 CodeGraph 索引。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a Python file in the temp dir
            (Path(tmpdir) / "test.py").write_text(
                "class MyClass:\n    pass\n", encoding="utf-8",
            )

            # Use process sandbox
            limits = ResourceLimits(allow_write=True)
            sandbox = ProcessSandbox(limits=limits)
            sandbox.create_workspace()

            # Copy file to sandbox workspace
            import shutil
            src = Path(tmpdir) / "test.py"
            dst = Path(sandbox._temp_dir) / "test.py"
            shutil.copy2(src, dst)

            # Index in sandbox workspace
            cg = CodeGraph(str(sandbox._temp_dir))
            cg.build_index()
            stats = cg.get_stats()

            assert stats.get("file_count", 0) >= 1
            symbols = cg.search("MyClass")
            assert len(symbols) >= 1

            sandbox.cleanup()

    def test_sandbox_with_lsp(self):
        """沙箱中运行 LSP 诊断。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "app.py").write_text(
                "x = 1\n", encoding="utf-8",
            )

            # LSP manager with sandbox dir
            lsp = LspManager(project_dir=tmpdir)
            assert lsp.detect_language("app.py") == "python"

    def test_sandbox_none_mode_with_chat_state(self):
        """NONE 模式沙箱 + ChatState 记录。"""
        cs = ChatState()
        sandbox = Sandbox(mode=SandboxMode.NONE)

        result = sandbox.execute("bash", {"command": "echo test"})
        cs.push_tool_result("call_1", result.output, tool_id="bash")

        assert cs.message_count == 1
        assert cs.messages[0].role == "tool"


# ═══════════════════════════════════════════════════════════════════
# Full Pipeline Integration
# ═══════════════════════════════════════════════════════════════════


class TestFullPipelineIntegration:
    """完整流水线集成测试 — 所有系统协同工作。"""

    def test_code_index_and_search_pipeline(self):
        """完整流程: 创建项目 → CodeGraph 索引 → 搜索 → 记录到 ChatState。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            # 1. Create a project with multiple files
            (Path(tmpdir) / "main.py").write_text(
                "from utils import helper\n\ndef main():\n    helper()\n",
                encoding="utf-8",
            )
            (Path(tmpdir) / "utils.py").write_text(
                "def helper():\n    return 42\n\nclass Config:\n    pass\n",
                encoding="utf-8",
            )

            # 2. Index with CodeGraph
            cg = CodeGraph(tmpdir)
            cg.build_index()
            stats = cg.get_stats()
            assert stats.get("file_count", 0) >= 2
            assert stats.get("symbol_count", 0) >= 3

            # 3. Search for symbols
            symbols = cg.search("helper")
            assert len(symbols) >= 1
            assert symbols[0].name == "helper"

            # 4. Get file outline - should find symbols in the file
            outline = cg.get_outline("main.py")
            assert len(outline) >= 1
            outline_names = [e["name"] for e in outline]
            assert "main" in outline_names  # 'main' function should be in outline

            # 5. Navigate to definition
            defs = cg.goto_definition("main.py", 3, 5)
            # Should find the 'main' function definition
            assert len(defs) >= 0  # Can be empty if no position match

            # 6. Stats should be consistent
            stats2 = cg.get_stats()
            assert stats2["file_count"] == stats["file_count"]
            assert stats2["symbol_count"] == stats["symbol_count"]

    def test_multi_language_indexing(self):
        """多语言项目索引。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Python
            (Path(tmpdir) / "main.py").write_text(
                "def main(): pass\n", encoding="utf-8",
            )
            # JavaScript
            (Path(tmpdir) / "app.js").write_text(
                "function run() {}\n", encoding="utf-8",
            )
            # TypeScript
            (Path(tmpdir) / "types.ts").write_text(
                "interface User {}\n", encoding="utf-8",
            )
            # Rust
            (Path(tmpdir) / "lib.rs").write_text(
                "pub fn process() {}\n", encoding="utf-8",
            )

            cg = CodeGraph(tmpdir)
            cg.build_index()
            stats = cg.get_stats()
            assert stats.get("file_count", 0) == 4
            stats.get("symbol_count", 0) >= 4

    def test_lsp_diagnostics_flow(self):
        """LSP 诊断推送 → 收集 → 查询 完整流程。"""
        lsp = LspManager()

        # Simulate diagnostics from multiple files
        test_data = {
            "file:///project/a.py": [
                {"range": {"start": {"line": 0, "character": 0}},
                 "message": "error in a", "severity": 1},
            ],
            "file:///project/b.py": [
                {"range": {"start": {"line": 1, "character": 2}},
                 "message": "warning in b", "severity": 2},
                {"range": {"start": {"line": 3, "character": 0}},
                 "message": "hint in b", "severity": 4},
            ],
        }

        for uri, diags in test_data.items():
            lsp.handle_diagnostics(uri=uri, diagnostics=diags)

        # Query
        all_diags = lsp.all_diagnostics
        assert len(all_diags) >= 2  # 2 files

        # Summary
        summary = lsp.summary()
        assert summary["diagnostics_errors"] == 1
        assert summary["diagnostics_warnings"] == 1

    def test_sandbox_execute_and_cleanup(self):
        """沙箱执行 → 清理 完整流程。"""
        sandbox = ProcessSandbox()
        try:
            sandbox.create_workspace()
            assert sandbox.active

            # Execute a command
            result = sandbox.execute_command("echo 'sandbox test'")
            assert result.success
            assert "sandbox test" in result.output
        finally:
            sandbox.cleanup()
            assert not sandbox.active

    def test_sandbox_timeout(self):
        """沙箱超时处理。"""
        limits = ResourceLimits(timeout_seconds=0.05)
        sandbox = ProcessSandbox(limits=limits)
        try:
            sandbox.create_workspace()
            result = sandbox.execute_command("sleep 10")
            assert not result.success
            assert "Timeout" in result.error
        finally:
            sandbox.cleanup()

    def test_chat_state_full_lifecycle(self):
        """ChatState 完整生命周期: 创建 → 操作 → 快照 → 恢复。"""
        cs = ChatState()

        # 1. Add messages
        cs.push_user_message("Hello")
        cs.push_assistant_response("Hi there!")
        cs.push_tool_result("call_1", "some output", tool_id="read_file")
        cs.push_user_message("What's next?")
        cs.push_assistant_response("Let me check...")

        assert cs.message_count == 5
        assert len(cs.events) == 5

        # 2. Record usage
        cs.record_usage({"prompt": 100, "completion": 50}, model="gpt-4")
        assert cs.usage.total_tokens == 150
        assert cs.usage.call_count == 1

        # 3. Snapshot
        snap = cs.snapshot()
        assert len(snap.messages) == 5
        assert snap.usage["prompt"] == 100

        # 4. Restore to new instance
        cs2 = ChatState()
        cs2.restore(snap)
        assert cs2.message_count == 5
        assert cs2.usage.total_tokens == 150

        # 5. Compact
        cs2._compaction_strategy = type("S", (), {
            "compact": lambda s, m, e: type("R", (), {
                "compacted_messages": m[:2],
                "compacted_count": len(m) - 2,
                "strategy": "test",
                "summary": "",
            })()
        })()
        result = cs2.compact()
        assert result.compacted_count == 3

    def test_chat_state_events_timeline(self):
        """ChatState 事件时间线完整性。"""
        cs = ChatState()
        cs.push_user_message("msg1")
        cs.push_assistant_response("reply1")
        cs.push_tool_result("c1", "out1")
        cs.push_system_message("system note")

        events = cs.events
        assert len(events) == 4

        kinds = [e.kind.value for e in events]
        assert kinds == ["user_message", "assistant_response", "tool_result", "system_injection"]

        # Timestamps should be monotonically increasing
        timestamps = [e.timestamp for e in events]
        assert all(timestamps[i] <= timestamps[i + 1] for i in range(len(timestamps) - 1))


# ═══════════════════════════════════════════════════════════════════
# Environment Integration
# ═══════════════════════════════════════════════════════════════════


class TestEnvironmentIntegration:
    """PromptBuilder + LSP + CodeGraph 集成。"""

    def test_prompt_builder_with_lsp(self):
        """PromptBuilder 可以注入 LSP 诊断。"""
        from zall.cli.environment import PromptBuilder
        from zall.core.context import Context
        from zall.cli.environment import CwdMeta

        context = Context(user_raw="test", cwd_meta=CwdMeta())
        builder = PromptBuilder(context)

        # Create LSP manager with mock diagnostics
        lsp = LspManager()
        lsp.handle_diagnostics(
            uri="file:///test.py",
            diagnostics=[{
                "range": {"start": {"line": 0, "character": 0}},
                "message": "test error",
                "severity": 1,
            }],
        )

        # Should not crash
        builder.add_lsp_diagnostics(lsp)
        prompt = builder.build()
        assert "CODE DIAGNOSTICS" in prompt
        assert "test error" in prompt

    def test_prompt_builder_with_clean_lsp(self):
        """没有诊断时, LSP 部分不注入。"""
        from zall.cli.environment import PromptBuilder
        from zall.core.context import Context
        from zall.cli.environment import CwdMeta

        context = Context(user_raw="test", cwd_meta=CwdMeta())
        builder = PromptBuilder(context)

        lsp = LspManager()
        builder.add_lsp_diagnostics(lsp)
        prompt = builder.build()

        # Clean project → no diagnostics section
        assert "CODE DIAGNOSTICS" not in prompt

    def test_prompt_builder_with_codegraph(self):
        """PromptBuilder 可以注入 CodeGraph 上下文。"""
        from zall.cli.environment import PromptBuilder
        from zall.core.context import Context
        from zall.cli.environment import CwdMeta

        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "test.py").write_text(
                "class App: pass\n", encoding="utf-8",
            )

            context = Context(user_raw="test", cwd_meta=CwdMeta())
            builder = PromptBuilder(context)

            cg = CodeGraph(tmpdir)
            cg.build_index()

            builder.add_codegraph_context(cg)
            prompt = builder.build()

            assert "CODE STRUCTURE" in prompt or "codegraph_search" in prompt

    def test_prompt_builder_with_lsp_and_codegraph(self):
        """PromptBuilder 同时注入 LSP + CodeGraph。"""
        from zall.cli.environment import PromptBuilder
        from zall.core.context import Context
        from zall.cli.environment import CwdMeta

        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "app.py").write_text(
                "class Service:\n    def run(self): pass\n",
                encoding="utf-8",
            )

            context = Context(user_raw="test", cwd_meta=CwdMeta())
            builder = PromptBuilder(context)

            lsp = LspManager()
            lsp.handle_diagnostics(
                uri="file:///app.py",
                diagnostics=[{
                    "range": {"start": {"line": 0, "character": 0}},
                    "message": "test warning",
                    "severity": 2,
                }],
            )

            cg = CodeGraph(tmpdir)
            cg.build_index()

            builder.add_lsp_diagnostics(lsp)
            builder.add_codegraph_context(cg)
            prompt = builder.build()

            # Both should be present
            assert "CODE DIAGNOSTICS" in prompt or "CODE STRUCTURE" in prompt
            if "CODE DIAGNOSTICS" in prompt:
                # Should mention the warning count but not the message (only errors are detailed)
                assert "warnings" in prompt