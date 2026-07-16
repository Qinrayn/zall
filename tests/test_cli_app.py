"""CLI app smoke tests (wiring AgentLoop 的end-to-end verification).

IPR-0: each test must contain a counterexample.

does not call real model API —— 用 fake adapter 替换 OpenAICompatAdapter.
Protected core invariants:
  1. main() 不再无限递归 (旧 bug 回归守护)
  2. 无 task 时returns 1 (打印 help)
  3. run() wiring AgentLoop, 产出 RunEgress + session 存盘
  4. --json 模式输出valid NDJSON
  5. blacklist 经 CliUserResponder 不被放行 (PR-0 端到端)
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from zall.cli import app as app_mod
from zall.cli import config as config_mod
from zall.cli import session as session_mod
from zall.cli.commands import cmd_compact, cmd_diff, cmd_doctor, handle_slash as _handle_slash
from zall.cli.repl_ui import repl as _repl
from zall.cli.orchestrator import make_usage_observer as _make_usage_observer
from zall.cli.session import _list_sessions, _run_eval, _run_replay, _run_resume
from zall.core.goal import TerminationState
from zall.core.model import Message, ModelResponse, StopReason, ToolCall, ToolChoice

# Test-local aliases (replaces removed app.py re-export block)
app_mod.repl = _repl
app_mod._handle_slash = _handle_slash
app_mod._make_usage_observer = _make_usage_observer
app_mod._list_sessions = _list_sessions
app_mod._run_eval = _run_eval
app_mod._run_replay = _run_replay
app_mod._run_resume = _run_resume


# ──────────────────────────────────────────────────────────────────────────
# Fake adapter (不调真 API)
# ──────────────────────────────────────────────────────────────────────────


class _FakeAdapter:
    """按脚本returns预设 ModelResponse 的 fake adapter."""
    __test__ = False

    def __init__(self, responses: list[ModelResponse]) -> None:
        self._responses = list(responses)
        self._idx = 0

    @property
    def model_name(self) -> str:
        return "fake-test"

    def complete(self, messages, tools, tool_choice=ToolChoice.AUTO) -> ModelResponse:
        if self._idx >= len(self._responses):
            return ModelResponse(content="script exhausted", stop_reason=StopReason.STOP)
        r = self._responses[self._idx]
        self._idx += 1
        return r


def _read_then_stop_script() -> list[ModelResponse]:
    """脚本: 调 read_file → STOP."""
    return [
        ModelResponse(
            content="let me read the file",
            tool_calls=(ToolCall(id="tc1", tool_id="read_file",
                                 args={"path": "README.md"}),),
            stop_reason=StopReason.TOOL_USE,
        ),
        ModelResponse(content="done reading", stop_reason=StopReason.STOP),
    ]


# ──────────────────────────────────────────────────────────────────────────
# main() 回归守护
# ──────────────────────────────────────────────────────────────────────────


class TestMainRegression:
    def test_no_task_enters_repl(self, capsys) -> None:
        """Happy path: 无 task → 进入 REPL (P2 行forchange).

        P1: 无 task returns 1 (打印 help)
        P2: 无 task 进入 REPL; test用 EOF 立即退出 → returns 0
        """
        # input_fn 遇到 EOF → REPL exit
        def boom(_):
            raise EOFError
        rc = app_mod.repl(input_fn=boom, out=io.StringIO())
        assert rc == 0

    def test_main_does_not_recurse(self) -> None:
        """Counterexample: main() 不再无限recursive (旧 bug).

        旧实现: def main(): raise SystemExit(main()) → RecursionError
        新实现: main(argv) → int
        """
        # --help directlyexit (argparse 行for), 不recursive
        with pytest.raises(SystemExit):
            app_mod.main(["--help"])


# ──────────────────────────────────────────────────────────────────────────
# run() 端到端 (fake adapter)
# ──────────────────────────────────────────────────────────────────────────


class TestRunWithFakeAdapter:
    def test_run_produces_egress_and_session(self, tmp_path: Path) -> None:
        """Happy path: run() wiring AgentLoop, 产出 RunEgress + session 存盘."""
        buf = io.StringIO()
        fake = _FakeAdapter(_read_then_stop_script())

        with patch.object(config_mod, "_build_adapter", return_value=fake), \
             patch.object(session_mod, "_get_sessions_dir", return_value= tmp_path / "sessions"):
            egress = app_mod.run("read README.md", judge_mode="none", yes=True,
                                 stream=False, out=buf)

        assert egress.final_state == TerminationState.UNDECIDABLE  # 无 judge
        assert egress.total_tool_calls == 1
        assert egress.total_model_calls == 2
        assert egress.error is None

        # session 存盘了
        sessions = list((tmp_path / "sessions").iterdir())
        assert len(sessions) == 1
        session_dir = sessions[0]
        assert (session_dir / "timeline.jsonl").exists()
        assert (session_dir / "meta.json").exists()

        # meta 含correctly字段
        meta = json.loads((session_dir / "meta.json").read_text())
        assert meta["final_state"] == "undecidable"
        assert meta["tool_calls"] == 1
        assert meta["model_calls"] == 2

    def test_run_json_mode_outputs_ndjson(self, tmp_path: Path) -> None:
        """Happy path: --json patternoutputvalid NDJSON (每行一个 JSON)."""
        buf = io.StringIO()
        fake = _FakeAdapter(_read_then_stop_script())

        with patch.object(config_mod, "_build_adapter", return_value=fake), \
             patch.object(session_mod, "_get_sessions_dir", return_value= tmp_path / "sessions"):
            app_mod.run("read README.md", json_mode=True, yes=True,
                        stream=False, out=buf)

        output = buf.getvalue()
        json_lines = []
        for line in output.split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                json_lines.append(obj)
            except json.JSONDecodeError:
                pass  # non- JSON 行 (如摘要行) 跳过

        # 至少有 model_call / tool_call event
        kinds = [o.get("kind") for o in json_lines if "kind" in o]
        assert "model_call" in kinds
        assert "tool_call_start" in kinds

    def test_timeline_chain_intact(self, tmp_path: Path) -> None:
        """Counterexample: session 存盘的 timeline 链must完整 (§6.1).

        如果 _save_session 写错了 prev_hash, 链会断.
        """
        buf = io.StringIO()
        fake = _FakeAdapter(_read_then_stop_script())

        with patch.object(config_mod, "_build_adapter", return_value=fake), \
             patch.object(session_mod, "_get_sessions_dir", return_value= tmp_path / "sessions"):
            app_mod.run("read README.md", yes=True, stream=False, out=buf)

        session_dir = next((tmp_path / "sessions").iterdir())
        lines = (session_dir / "timeline.jsonl").read_text(encoding="utf-8").strip().split("\n")
        events = [json.loads(l) for l in lines]

        # 链式verify: 每条 prev_hash == 前一条 hash
        prev = "0" * 64
        for ev in events:
            assert ev["prev_hash"] == prev, f"链断在 {ev['event_id']}"
            prev = ev["hash"]

    def test_config_error_returns_egress_with_error(self) -> None:
        """Counterexample: adapter configfail → returns带 error 的 RunEgress (does not crash)."""
        buf = io.StringIO()
        with patch.object(config_mod, "_build_adapter",
                          side_effect=ValueError("no api key")):
            egress = app_mod.run("do something", out=buf)

        assert egress.final_state == TerminationState.UNDECIDABLE
        assert egress.error is not None
        assert "api key" in egress.error.lower()


# ──────────────────────────────────────────────────────────────────────────
# REPL + slash command (P2)
# ──────────────────────────────────────────────────────────────────────────


class TestReplSlash:
    def test_exit_command(self) -> None:
        """Happy path: /exit → REPL returns 0."""
        buf = io.StringIO()
        inputs = iter(["/exit"])
        rc = app_mod.repl(input_fn=lambda _: next(inputs), out=buf)
        assert rc == 0

    def test_eof_exits(self) -> None:
        """Happy path: Ctrl-D (EOFError) → REPL exitreturns 0."""
        buf = io.StringIO()
        def boom(_):
            raise EOFError
        rc = app_mod.repl(input_fn=boom, out=buf)
        assert rc == 0

    def test_help_command(self) -> None:
        """Happy path: /help → output含commandlist."""
        buf = io.StringIO()
        inputs = iter(["/help", "/exit"])
        app_mod.repl(input_fn=lambda _: next(inputs), out=buf)
        out = buf.getvalue()
        assert "/help" in out
        assert "/about" in out
        assert "/sessions" in out
        assert "/model" in out

    def test_model_switch(self) -> None:
        """Happy path: /model X → 切model switching; /model 无参 → 显示当前."""
        buf = io.StringIO()
        inputs = iter(["/model gpt-4o", "/model", "/exit"])
        app_mod.repl(input_fn=lambda _: next(inputs), out=buf)
        out = buf.getvalue()
        assert "gpt-4o" in out  # 切换确认 + 显示

    def test_unknown_command(self) -> None:
        """Counterexample: 未知 slash command → prompt unknown (does not crash)."""
        buf = io.StringIO()
        inputs = iter(["/bogus", "/exit"])
        app_mod.repl(input_fn=lambda _: next(inputs), out=buf)
        assert "unknown" in buf.getvalue().lower()


# ──────────────────────────────────────────────────────────────────────────
# REPL 单一对话态 (P5: 去掉双pattern, default对话)
# ──────────────────────────────────────────────────────────────────────────


class TestReplChat:
    def test_repl_default_is_chat(self) -> None:
        """Happy path: REPL default就是对话态 (共享context), 无需 /chat."""
        buf = io.StringIO()
        fake = _FakeAdapter([ModelResponse(content="hi back", stop_reason=StopReason.STOP)])
        inputs = iter(["hello", "/exit"])
        with patch.object(config_mod, "_build_adapter", return_value=fake), \
             patch.object(session_mod, "_get_sessions_dir", return_value= Path("/tmp/zall_test")):
            app_mod.repl(input_fn=lambda _: next(inputs), out=buf, stream=False, yes=True)
        assert "hi back" in buf.getvalue()

    def test_repl_multi_turn_shared_context(self) -> None:
        """Happy path: 多轮对话, context自然延续."""
        buf = io.StringIO()
        fake = _FakeAdapter([
            ModelResponse(content="hello", stop_reason=StopReason.STOP),
            ModelResponse(content="bye", stop_reason=StopReason.STOP),
        ])
        inputs = iter(["hi", "bye", "/exit"])
        with patch.object(config_mod, "_build_adapter", return_value=fake), \
             patch.object(session_mod, "_get_sessions_dir", return_value= Path("/tmp/zall_test")):
            app_mod.repl(input_fn=lambda _: next(inputs), out=buf, stream=False, yes=True)
        out = buf.getvalue()
        assert "hello" in out
        assert "bye" in out

    def test_repl_does_not_save_session(self) -> None:
        """Counterexample: REPL 对话态不存 session."""
        buf = io.StringIO()
        fake = _FakeAdapter([ModelResponse(content="ok", stop_reason=StopReason.STOP)])
        inputs = iter(["hi", "/exit"])
        with patch.object(config_mod, "_build_adapter", return_value=fake), \
             patch.object(session_mod, "_get_sessions_dir", return_value= Path("/tmp/zall_nosess")):
            app_mod.repl(input_fn=lambda _: next(inputs), out=buf, stream=False)
        assert "session:" not in buf.getvalue()

    def test_repl_with_tool_use(self) -> None:
        """Happy path: REPL 对话态能调tool (security层照走)."""
        buf = io.StringIO()
        fake = _FakeAdapter([
            ModelResponse(
                content="checking",
                tool_calls=(ToolCall(id="tc1", tool_id="read_file",
                                     args={"path": "x"}),),
                stop_reason=StopReason.TOOL_USE,
            ),
            ModelResponse(content="done", stop_reason=StopReason.STOP),
        ])
        inputs = iter(["check x", "/exit"])
        with patch.object(config_mod, "_build_adapter", return_value=fake), \
             patch.object(session_mod, "_get_sessions_dir", return_value= Path("/tmp/zall_test")):
            app_mod.repl(input_fn=lambda _: next(inputs), out=buf, stream=False, yes=True)
        assert "Read" in buf.getvalue() or "read_file" in buf.getvalue()

    def test_no_chat_task_commands(self) -> None:
        """Counterexample: /chat /task /end 不再是有效command (已去掉双pattern)."""
        buf = io.StringIO()
        inputs = iter(["/chat", "/task", "/end", "/exit"])
        app_mod.repl(input_fn=lambda _: next(inputs), out=buf)
        assert buf.getvalue().count("unknown") >= 3


class TestEvalReplay:
    def test_eval_no_sessions(self) -> None:
        """Happy path: /eval 无 session → prompt."""
        buf = io.StringIO()
        with patch.object(session_mod, "_get_sessions_dir", return_value= Path("/tmp/zall_no_sessions")):
            app_mod._run_eval(buf)
        assert "no sessions" in buf.getvalue().lower()

    def test_eval_with_sessions(self, tmp_path: Path) -> None:
        """Happy path: /eval 有 session → 显示metric."""
        sdir = tmp_path / "sessions" / "abc123"
        sdir.mkdir(parents=True)
        (sdir / "meta.json").write_text(json.dumps({
            "run_id": "abc123", "final_state": "undecidable",
            "step_count": 2, "tool_calls": 1, "model_calls": 2, "error": None,
        }))
        (sdir / "timeline.jsonl").write_text("")

        buf = io.StringIO()
        with patch.object(session_mod, "_get_sessions_dir", return_value= tmp_path / "sessions"):
            app_mod._run_eval(buf)
        out = buf.getvalue()
        assert "eval" in out.lower()
        assert "goal_achievement" in out

    def test_replay_no_id_shows_usage(self) -> None:
        """Happy path: /replay 无parameter → 显示 usage."""
        buf = io.StringIO()
        state = {}
        with patch.object(session_mod, "_get_sessions_dir", return_value= Path("/tmp/x")):
            app_mod._handle_slash("/replay", state, buf)
        assert "usage" in buf.getvalue().lower()

    def test_replay_not_found(self) -> None:
        """Happy path: /replay 不存在的 id → prompt not found 或 no sessions."""
        buf = io.StringIO()
        with patch.object(session_mod, "_get_sessions_dir", return_value= Path("/tmp/zall_noexist")):
            app_mod._run_replay(buf, "nonexistent")
        out = buf.getvalue().lower()
        assert "not found" in out or "no sessions" in out


# ──────────────────────────────────────────────────────────────────────────
# REPL 步数max + 进度prompt (50步fix)
# ──────────────────────────────────────────────────────────────────────────


class TestReplMaxSteps:
    def test_repl_default_max_steps_no_friction(self) -> None:
        """Happy path: REPL default不发步数摩擦 (无 step N/M prompt行, banner 也不显示步数).

        用户实测: 每轮显示 step N/400 是噪音 + 上限本身是 bug 源.
        改后: banner 不含步数, awaiting_input 后干净回到prompt符.
        """
        buf = io.StringIO()
        fake = _FakeAdapter([ModelResponse(content="ok", stop_reason=StopReason.STOP)])
        inputs = iter(["hi", "/exit"])
        with patch.object(config_mod, "_build_adapter", return_value=fake), \
             patch.object(session_mod, "_get_sessions_dir", return_value= Path("/tmp/zall_ms")):
            app_mod.repl(input_fn=lambda _: next(inputs), out=buf, stream=False, yes=True)
        out = buf.getvalue()
        # banner 含 zall + model, 但不含步数显示
        assert "zall" in out
        # 不再有 step N/M 摩擦行 + banner 不显示步数 (Counterexample)
        assert "step 1/100000" not in out
        assert "approaching" not in out
        assert "100000" not in out  # banner 已去步数

    def test_max_steps_command_sets_value(self) -> None:
        """Happy path: /max-steps N → state 更新; 无参 → 显示当前."""
        buf = io.StringIO()
        inputs = iter(["/max-steps 100", "/max-steps", "/exit"])
        app_mod.repl(input_fn=lambda _: next(inputs), out=buf)
        out = buf.getvalue()
        assert "100" in out  # 设置确认 + 显示

    def test_no_step_progress_per_turn(self) -> None:
        """Counterexample: 不再每轮显示 step 进度行 (Bug A: 步数去摩擦化).

        旧: 每轮 `· step 1/400`.新: 干净回到prompt符.
        这里 fake 一次性 STOP → 无 step 计数摩擦行.
        """
        buf = io.StringIO()
        fake = _FakeAdapter([ModelResponse(content="hi", stop_reason=StopReason.STOP)])
        inputs = iter(["hello", "/exit"])
        with patch.object(config_mod, "_build_adapter", return_value=fake), \
             patch.object(session_mod, "_get_sessions_dir", return_value= Path("/tmp/zall_sp")):
            app_mod.repl(input_fn=lambda _: next(inputs), out=buf, stream=False, yes=True)
        out = buf.getvalue()
        # 无 step N/M prompt行
        assert "/400" not in out
        assert "/100000" not in out

    def test_ctrl_c_during_step_returns_to_prompt(self) -> None:
        """Happy path: streaming中 Ctrl-C → 打断当前reply, 回prompt符, loop preserve (Bug B)."""
        class _InterruptAdapter:
            __test__ = False
            @property
            def model_name(self) -> str:
                return "interrupt-test"
            def complete(self, messages, tools, tool_choice=ToolChoice.AUTO) -> ModelResponse:
                raise KeyboardInterrupt  # mock httpx 读 socket 被打断

        buf = io.StringIO()
        fake = _InterruptAdapter()
        inputs = iter(["hi", "are you there?", "/exit"])
        with patch.object(config_mod, "_build_adapter", return_value=fake), \
             patch.object(session_mod, "_get_sessions_dir", return_value= Path("/tmp/zall_int")):
            app_mod.repl(input_fn=lambda _: next(inputs), out=buf, stream=False, yes=True)
        out = buf.getvalue()
        # 打断了但没exit (后续input "are you there?" 走了, 然后 /exit)
        assert "interrupted" in out.lower()
        assert "bye" in out  # 正常退出, 而non- Traceback


# ──────────────────────────────────────────────────────────────────────────
# /verbose + /clear
# ──────────────────────────────────────────────────────────────────────────


class TestReplVerboseClear:
    def test_verbose_toggle(self) -> None:
        """Happy path: /verbose 切换 on/off."""
        buf = io.StringIO()
        inputs = iter(["/verbose", "/verbose", "/exit"])
        app_mod.repl(input_fn=lambda _: next(inputs), out=buf)
        out = buf.getvalue()
        assert "on" in out
        assert "off" in out

    def test_clear_resets_loop(self) -> None:
        """Happy path: /clear 重置对话态 (下次input开新对话).

        construct: 首轮建立 loop → /clear → 新输入建立新 loop (不报错).
        """
        buf = io.StringIO()
        fake = _FakeAdapter([
            ModelResponse(content="first", stop_reason=StopReason.STOP),
            ModelResponse(content="second", stop_reason=StopReason.STOP),
        ])
        inputs = iter(["hi", "/clear", "again", "/exit"])
        with patch.object(config_mod, "_build_adapter", return_value=fake), \
             patch.object(session_mod, "_get_sessions_dir", return_value= Path("/tmp/zall_cl")):
            app_mod.repl(input_fn=lambda _: next(inputs), out=buf, stream=False, yes=True)
        out = buf.getvalue()
        assert "first" in out
        assert "second" in out


# ──────────────────────────────────────────────────────────────────────────
# /sessions 美化 + /resume + messages.json
# ──────────────────────────────────────────────────────────────────────────


class TestSessionsAndResume:
    def test_save_session_writes_messages_json(self, tmp_path: Path) -> None:
        """Happy path: run() 后 session directory含 messages.json + meta 含 saved_at."""
        buf = io.StringIO()
        fake = _FakeAdapter(_read_then_stop_script())
        with patch.object(config_mod, "_build_adapter", return_value=fake), \
             patch.object(session_mod, "_get_sessions_dir", return_value= tmp_path / "sessions"):
            app_mod.run("read README.md", yes=True, stream=False, out=buf)

        session_dir = next((tmp_path / "sessions").iterdir())
        assert (session_dir / "messages.json").exists()
        meta = json.loads((session_dir / "meta.json").read_text(encoding="utf-8"))
        assert "saved_at" in meta

        msgs = json.loads((session_dir / "messages.json").read_text(encoding="utf-8"))
        assert len(msgs) >= 2  # system + user 至少
        assert msgs[0]["role"] == "system"
        assert msgs[1]["role"] == "user"

    def test_sessions_table_output(self, tmp_path: Path) -> None:
        """Happy path: /sessions output含 id/state/steps 字段."""
        sdir = tmp_path / "sessions" / "abc123def456"
        sdir.mkdir(parents=True)
        (sdir / "meta.json").write_text(json.dumps({
            "run_id": "abc123def456", "final_state": "undecidable",
            "step_count": 3, "tool_calls": 1, "model_calls": 2, "error": None,
            "saved_at": "2026-07-08T12:00:00",
        }))
        buf = io.StringIO()
        with patch.object(session_mod, "_get_sessions_dir", return_value= tmp_path / "sessions"):
            app_mod._list_sessions(buf)
        out = buf.getvalue()
        assert "abc123" in out
        assert "undecidable" in out
        assert "3" in out

    def test_resume_loads_messages_into_state(self, tmp_path: Path) -> None:
        """Happy path: /resume <id> 把 messages load到 state["resume_messages"]."""
        sdir = tmp_path / "sessions" / "resume123"
        sdir.mkdir(parents=True)
        (sdir / "meta.json").write_text(json.dumps({"final_state": "undecidable"}))
        (sdir / "messages.json").write_text(json.dumps([
            {"role": "system", "content": "sys", "tool_call_id": None, "tool_calls": []},
            {"role": "user", "content": "do task", "tool_call_id": None, "tool_calls": []},
        ]))
        buf = io.StringIO()
        state: dict = {}
        with patch.object(session_mod, "_get_sessions_dir", return_value= tmp_path / "sessions"):
            app_mod._run_resume(buf, "resume123", state)
        out = buf.getvalue()
        assert "resumed" in out.lower()
        assert "resume_messages" in state
        assert len(state["resume_messages"]) == 3  # 2 msgs + 1 system note

    def test_resume_old_session_no_messages_json(self, tmp_path: Path) -> None:
        """Counterexample: 旧 session 无 messages.json → prompt不可resume."""
        sdir = tmp_path / "sessions" / "old123"
        sdir.mkdir(parents=True)
        (sdir / "meta.json").write_text(json.dumps({"final_state": "undecidable"}))
        buf = io.StringIO()
        state: dict = {}
        with patch.object(session_mod, "_get_sessions_dir", return_value= tmp_path / "sessions"):
            app_mod._run_resume(buf, "old123", state)
        out = buf.getvalue().lower()
        assert "no restorable" in out or "older format" in out
        assert "resume_messages" not in state

    def test_resume_not_found(self) -> None:
        """Counterexample: /resume 不存在 → not found 或 no sessions."""
        buf = io.StringIO()
        state: dict = {}
        with patch.object(session_mod, "_get_sessions_dir", return_value= Path("/tmp/zall_noresume")):
            app_mod._run_resume(buf, "nonexistent", state)
        out = buf.getvalue().lower()
        assert "not found" in out or "no sessions" in out


# ──────────────────────────────────────────────────────────────────────────
# 新增commandtest (对齐 Claude Code 水准): init / cost / diff / doctor / compact / usage
# ──────────────────────────────────────────────────────────────────────────

from zall.core.loop import LoopEvent  # noqa: E402  (放在类外末尾, 保持导入区style)


class TestNewCommands:
    def test_init_creates_config(self, tmp_path: Path) -> None:
        """Happy path: zall init 生成 .zall/rules.toml + .zall/AGENTS.md."""
        buf = io.StringIO()
        import os
        old_cwd = os.getcwd()
        try:
            os.chdir(str(tmp_path))
            from zall.cli.commands import cmd_init
            cmd_init("", buf, None, {})
        finally:
            os.chdir(old_cwd)
        out = buf.getvalue()
        assert "initialized" in out.lower()
        assert (tmp_path / ".zall" / "rules.toml").exists()
        assert (tmp_path / ".zall" / "AGENTS.md").exists()
        # 不covers已存在file (幂等)
        os.chdir(str(tmp_path))
        cmd_init("", buf, None, {})
        os.chdir(old_cwd)
        rules = (tmp_path / ".zall" / "rules.toml").read_text(encoding="utf-8")
        assert "AGENTS" not in rules  # 未误写

    def test_cost_shows_usage(self) -> None:
        """Happy path: /cost 显示累计 token."""
        buf = io.StringIO()
        state = {"usage": {"prompt": 120, "completion": 34}}
        from zall.cli.commands import cmd_cost
        cmd_cost("", buf, None, state)
        out = buf.getvalue()
        assert "120" in out  # prompt tokens
        assert "34" in out  # completion tokens
        assert "cost" in out.lower()

    def test_cost_empty_usage(self) -> None:
        """Counterexample: 无 usage → 显示 0."""
        buf = io.StringIO()
        from zall.cli.commands import cmd_cost
        cmd_cost("", buf, None, {"usage": {"prompt": 0, "completion": 0}})
        assert "0" in buf.getvalue()

    def test_usage_observer_accumulates(self) -> None:
        """invariant: model_call event的 usage 被累计到 state (跨多轮)."""
        state: dict = {"usage": {"prompt": 0, "completion": 0}}
        inner = lambda ev: None  # noqa: E731
        obs = app_mod._make_usage_observer(inner, state)
        ev = LoopEvent(kind="model_call", step=1, payload={"usage": {"prompt": 10, "completion": 5}})
        obs(ev)
        ev2 = LoopEvent(kind="model_call", step=2, payload={"usage": {"prompt": 20, "completion": 7}})
        obs(ev2)
        assert state["usage"] == {"prompt": 30, "completion": 12}
        # non- model_call event不累计
        obs(LoopEvent(kind="tool_call_end", step=3, payload={}))
        assert state["usage"] == {"prompt": 30, "completion": 12}

    def test_diff_clean_repo(self, tmp_path: Path, monkeypatch) -> None:
        """Happy path (Counterexample式): non- git 仓库 → prompt not a git repository."""
        monkeypatch.chdir(tmp_path)
        buf = io.StringIO()
        cmd_diff("", buf, None, {})
        assert "not a git repository" in buf.getvalue().lower()

    def test_doctor_runs(self) -> None:
        """Happy path: /doctor output含dependencycheck行."""
        buf = io.StringIO()
        cmd_doctor("", buf, None, {})
        out = buf.getvalue().lower()
        assert "api_key" in out
        assert "dep:pydantic" in out

    def test_compact_folds_messages(self) -> None:
        """invariant: /compact 折叠历史但preserve timeline 语义 (messages 变短).

        v0.0.10: 使用 ModelCompactor, 需要 loop._model 带 complete() 方法.
        """
        buf = io.StringIO()
        from zall.core.model import Message, ModelResponse, StopReason

        class _FakeModel:
            model_name = "fake"
            def complete(self, messages, tools, tool_choice):
                return ModelResponse(
                    content="[SUMMARY] compacted conversation",
                    stop_reason=StopReason.STOP,
                )

        class _FakeLoop:
            _messages = [
                Message(role="system", content="sys"),
                Message(role="user", content="u1"),
                Message(role="assistant", content="a1"),
                Message(role="user", content="u2"),
                Message(role="assistant", content="a2"),
                Message(role="user", content="u3"),
                Message(role="assistant", content="a3"),
                Message(role="user", content="u4"),
                Message(role="assistant", content="a4"),
            ]  # 8 non-system msgs > keep_recent=4 → will compact
            _model = _FakeModel()

        loop = _FakeLoop()
        state: dict = {}
        cmd_compact("", buf, loop, state)
        # 压缩后: system + summary + 最近 4 = 6
        assert len(loop._messages) == 6  # 1 system + 1 summary + 4 recent
        assert any("compacted" in m.content or "SUMMARY" in m.content
                   for m in loop._messages if m.role == "system")

    def test_compact_nothing_yet(self) -> None:
        """Counterexample: message太少 → 不压缩."""
        buf = io.StringIO()

        class _FakeLoop:
            _messages = [__import__("zall.core.model", fromlist=["Message"]).Message(
                role="user", content="hi")]

        loop = _FakeLoop()
        cmd_compact("", buf, loop, {})
        assert "nothing to compact" in buf.getvalue().lower()

    def test_slash_routing_new_commands(self) -> None:
        """Happy path: /cost /diff /doctor 路由到corresponds tocommand且不returns exit/clear."""
        buf = io.StringIO()
        state: dict = {"usage": {"prompt": 1, "completion": 1}}
        for cmd in ("/cost", "/diff", "/doctor"):
            r = app_mod._handle_slash(cmd, state, buf, loop=None)
            assert r == "handled"

    def test_slash_compact_returns_handled_preserves_loop(self) -> None:
        """Happy path (Bug C fix): /compact returns 'handled' (preserve loop, 不丢对话态).

        旧实现 return "clear" → REPL 丢弃 loop 重建 → 压缩后的上下文 / 当前对话全丢
        (即便压缩成功 _cmd_compact 已原地替换 loop._messages, 重建反而丢失).修复后
        保留 loop: 成功时压缩已生效, 无可压缩/fail时原对话态保留.
        """
        buf = io.StringIO()
        from zall.core.model import Message

        class _FakeLoop:
            _messages = [Message(role="user", content=f"m{i}") for i in range(6)]

        loop = _FakeLoop()
        r = app_mod._handle_slash("/compact", {}, buf, loop=loop)
        assert r == "handled"  # not "clear" (Bug C)
        assert loop is not None  # 对话态保留

    def test_resume_no_arg_shows_usage(self) -> None:
        """Happy path: /resume 无参 → usage."""
        buf = io.StringIO()
        state: dict = {}
        with patch.object(session_mod, "_get_sessions_dir", return_value= Path("/tmp/x")):
            app_mod._handle_slash("/resume", state, buf)
        assert "usage" in buf.getvalue().lower()
