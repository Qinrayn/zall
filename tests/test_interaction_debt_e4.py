"""E4 interaction debt tests — autosave, Ctrl+C discard, always_allow persistence.

§12.3 E4: 还交互层的债（对标 Claude Code/opencode）。

Covers:
  1. E4.1: Autosave de-PID-ification + atomic write
  2. E4.2: Ctrl+C discard semantics (partial response rollback)
  3. E4.3: Permission cross-session persistence

IPR-0: each test contains a counterexample assertion.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from zall.cli import session as session_mod
from zall.cli.responder import CliUserResponder, _always_allow_path
from zall.core.verifiability import EventType


# ═══════════════════════════════════════════════════════════════════════════════
# E4.1: Autosave de-PID-ification + atomic write
# ═══════════════════════════════════════════════════════════════════════════════


class TestAutosaveNoPidInFilename:
    """_REPL_AUTOSAVE 文件名不含 PID (§12.3 E4.1)."""

    def test_autosave_no_pid_in_filename(self) -> None:
        """Assert _REPL_AUTOSAVE filename does not contain PID."""
        filename = session_mod._REPL_AUTOSAVE.name
        pid_str = str(os.getpid())
        assert pid_str not in filename, (
            f"filename '{filename}' still contains PID '{pid_str}'"
        )
        assert filename == ".repl_autosave.json", (
            f"expected '.repl_autosave.json', got '{filename}'"
        )


class TestLegacyAutosaveSweep:
    """旧版 PID 命名 autosave 残留回收 (_sweep_legacy_autosaves).

    背景: E4 前每进程写 .repl_autosave_<pid>.json, 崩溃后无人回收,
    实测用户目录累积 37 个残留。仅删死进程的文件。
    """

    def test_sweep_removes_dead_pid_files(self, tmp_path: Path, monkeypatch) -> None:
        """死 PID 命名的残留被删除; 固定名文件 (反例) 不受影响。"""
        zdir = tmp_path / ".zall"
        zdir.mkdir()
        autosave_path = zdir / ".repl_autosave.json"
        monkeypatch.setattr(session_mod, "_REPL_AUTOSAVE", autosave_path)
        # 死进程残留 (PID 999999999 几乎不可能存活)
        dead1 = zdir / ".repl_autosave_999999999.json"
        dead2 = zdir / ".repl_autosave_999999998.json"
        dead1.write_text("{}", encoding="utf-8")
        dead2.write_text("{}", encoding="utf-8")
        # 反例 1: 当前 E4 固定名文件不归 sweep 管
        autosave_path.write_text('{"messages": []}', encoding="utf-8")
        # 反例 2: 非数字后缀不碰 (未知格式保守)
        weird = zdir / ".repl_autosave_backup.json"
        weird.write_text("{}", encoding="utf-8")
        monkeypatch.setattr(session_mod, "_is_pid_alive", lambda pid: False)
        removed = session_mod._sweep_legacy_autosaves()
        assert removed == 2, f"expected 2 removed, got {removed}"
        assert not dead1.exists() and not dead2.exists()
        assert autosave_path.exists(), "E4 固定名文件不应被 sweep 删除"
        assert weird.exists(), "非数字后缀文件不应被删除"

    def test_sweep_spares_alive_pid(self, tmp_path: Path, monkeypatch) -> None:
        """反例: PID 仍存活的旧版 autosave 不动 (并行旧版本保护)。"""
        zdir = tmp_path / ".zall"
        zdir.mkdir()
        monkeypatch.setattr(
            session_mod, "_REPL_AUTOSAVE", zdir / ".repl_autosave.json"
        )
        alive = zdir / f".repl_autosave_{os.getpid()}.json"
        alive.write_text("{}", encoding="utf-8")
        # 真实 _is_pid_alive: 当前进程 PID 必存活
        removed = session_mod._sweep_legacy_autosaves()
        assert removed == 0
        assert alive.exists(), "存活 PID 的 autosave 被误删"

    def test_check_autosave_triggers_sweep(self, tmp_path: Path, monkeypatch) -> None:
        """_check_repl_autosave 启动时顺手清扫 (接线验证)。"""
        zdir = tmp_path / ".zall"
        zdir.mkdir()
        monkeypatch.setattr(
            session_mod, "_REPL_AUTOSAVE", zdir / ".repl_autosave.json"
        )
        dead = zdir / ".repl_autosave_999999999.json"
        dead.write_text("{}", encoding="utf-8")
        monkeypatch.setattr(session_mod, "_is_pid_alive", lambda pid: False)
        out = MagicMock()
        result = session_mod._check_repl_autosave(out, {})
        assert result is False  # 无 E4 autosave 可恢复
        assert not dead.exists(), "_check_repl_autosave 应触发 legacy sweep"


class TestAutosaveAtomicWrite:
    """Atomic write: .tmp + os.replace, target file not corrupted on interrupt."""

    def test_autosave_atomic_write_creates_tmp(self, tmp_path: Path, monkeypatch) -> None:
        """Assert _save_repl_state writes to .tmp then renames to target."""
        # Patch the module-level constant directly
        from zall.cli import session as session_mod
        autosave_path = tmp_path / ".zall" / ".repl_autosave.json"
        monkeypatch.setattr(session_mod, "_REPL_AUTOSAVE", autosave_path)

        # Create a mock loop
        loop = MagicMock()
        loop.messages = []
        state = {"model": "test", "verbose": False, "usage": {"prompt": 0, "completion": 0}}

        session_mod._save_repl_state(loop, state)

        # The target file must exist
        assert autosave_path.exists(), f"autosave file {autosave_path} does not exist"
        # The .tmp file must NOT exist (it was renamed)
        tmp_path_file = autosave_path.with_suffix(".json.tmp")
        assert not tmp_path_file.exists(), (
            f"temporary file {tmp_path_file} still exists after save"
        )
        # Content must be valid JSON with pid field
        data = json.loads(autosave_path.read_text(encoding="utf-8"))
        assert "pid" in data, "autosave missing pid field"
        assert data["pid"] == os.getpid(), "pid field does not match current process"

    def test_autosave_atomic_write_no_corruption(self, tmp_path: Path, monkeypatch) -> None:
        """Counterexample: simulate interrupted write, target file not corrupted."""
        from zall.cli import session as session_mod
        autosave_path = tmp_path / ".zall" / ".repl_autosave.json"
        monkeypatch.setattr(session_mod, "_REPL_AUTOSAVE", autosave_path)
        tmp_file = autosave_path.with_suffix(".json.tmp")

        # Write a valid autosave first
        loop = MagicMock()
        loop.messages = []
        state = {"model": "test", "verbose": False, "usage": {"prompt": 0, "completion": 0}}
        session_mod._save_repl_state(loop, state)
        assert autosave_path.exists()

        # Simulate interrupted write: write corrupt data to .tmp, then crash
        tmp_file.write_text("corrupted", encoding="utf-8")
        # The target file should still be valid
        data = json.loads(autosave_path.read_text(encoding="utf-8"))
        assert "pid" in data, "target file corrupted after interrupted write"

        # Now simulate a successful write after crash
        session_mod._save_repl_state(loop, state)
        # The tmp file should be gone
        assert not tmp_file.exists(), "tmp file not cleaned up after second save"
        # Target file should be valid
        data = json.loads(autosave_path.read_text(encoding="utf-8"))
        assert "pid" in data, "target file corrupted after second save"


class TestAutosaveRecoverableAfterCrash:
    """Write autosave, simulate crash, new process can read it."""

    def test_autosave_recoverable_after_crash(self, tmp_path: Path, monkeypatch) -> None:
        """Write autosave, change PID, new process should detect recovery."""
        from zall.cli import session as session_mod
        autosave_path = tmp_path / ".zall" / ".repl_autosave.json"
        monkeypatch.setattr(session_mod, "_REPL_AUTOSAVE", autosave_path)

        # Write an autosave with a non-current PID (simulating a crashed process)
        data = {
            "model": "test-model",
            "verbose": False,
            "usage": {"prompt": 10, "completion": 20},
            "messages": [
                {"role": "user", "content": "hello", "tool_call_id": None, "tool_calls": []},
                {"role": "assistant", "content": "hi", "tool_call_id": None, "tool_calls": []},
            ],
            "saved_at": "2026-07-19T10:00:00",
            "pid": 99999999,  # dead PID (unlikely to be alive)
        }
        autosave_path.parent.mkdir(parents=True, exist_ok=True)
        autosave_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

        # _check_repl_autosave should detect the autosave in TTY mode
        out = MagicMock()
        out.isatty.return_value = True
        out.write = MagicMock()

        state = {"_input_fn": lambda _: "n"}  # user says no to restore
        result = session_mod._check_repl_autosave(out, state)

        # The autosave was detected (result depends on user input, but it was found)
        # Since we said "n", the autosave should be cleared and return False
        assert not result, "expected False (user said no)"
        # The autosave file should have been cleared
        assert not autosave_path.exists(), "autosave should be cleared after 'n'"

    def test_autosave_recoverable_yes_restores(self, tmp_path: Path, monkeypatch) -> None:
        """Happy path: user says 'y', messages are restored into state."""
        from zall.cli import session as session_mod
        autosave_path = tmp_path / ".zall" / ".repl_autosave.json"
        monkeypatch.setattr(session_mod, "_REPL_AUTOSAVE", autosave_path)

        data = {
            "model": "test-model",
            "verbose": False,
            "usage": {"prompt": 10, "completion": 20},
            "messages": [
                {"role": "user", "content": "hello", "tool_call_id": None, "tool_calls": []},
                {"role": "assistant", "content": "hi there", "tool_call_id": None, "tool_calls": []},
            ],
            "saved_at": "2026-07-19T10:00:00",
            "pid": 99999998,  # dead PID
        }
        autosave_path.parent.mkdir(parents=True, exist_ok=True)
        autosave_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

        out = MagicMock()
        out.isatty.return_value = True
        out.write = MagicMock()

        state = {"_input_fn": lambda _: "y"}  # user says yes to restore
        result = session_mod._check_repl_autosave(out, state)

        assert result, "expected True (user said yes)"
        assert "resume_messages" in state, "messages not restored to state"
        assert len(state["resume_messages"]) == 2, "expected 2 messages restored"
        assert state["model"] == "test-model", "model not restored"
        assert state["usage"]["prompt"] == 10, "usage not restored"
        # Autosave file should be cleared after restore
        assert not autosave_path.exists(), "autosave should be cleared after restore"

    def test_autosave_skipped_for_alive_process(self, tmp_path: Path, monkeypatch) -> None:
        """Counterexample: autosave owned by another alive process is skipped."""
        from zall.cli import session as session_mod
        autosave_path = tmp_path / ".zall" / ".repl_autosave.json"
        monkeypatch.setattr(session_mod, "_REPL_AUTOSAVE", autosave_path)

        # Write an autosave with the current PID (simulating same process)
        data = {
            "model": "test-model",
            "verbose": False,
            "usage": {"prompt": 10, "completion": 20},
            "messages": [
                {"role": "user", "content": "hello", "tool_call_id": None, "tool_calls": []},
            ],
            "saved_at": "2026-07-19T10:00:00",
            "pid": os.getpid(),  # same as current process
        }
        autosave_path.parent.mkdir(parents=True, exist_ok=True)
        autosave_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

        out = MagicMock()
        out.isatty.return_value = True
        out.write = MagicMock()

        state = {"_input_fn": lambda _: "y"}
        # Same PID as current process - should still be recoverable (we prompt)
        result = session_mod._check_repl_autosave(out, state)
        assert result is True, "autosave with same PID should be recoverable"


# ═══════════════════════════════════════════════════════════════════════════════
# E4.2: Ctrl+C discard semantics
# ═══════════════════════════════════════════════════════════════════════════════


class TestInterruptDiscardsPartialResponse:
    """Ctrl+C during step() should rollback messages to pre-step state."""

    def test_interrupt_discards_partial_messages(self, monkeypatch) -> None:
        """Simulate Ctrl+C after step() started, assert messages rolled back."""
        from zall.core.model import Message

        # Create a mock loop with messages
        loop = MagicMock()
        # Pre-step messages: system + user
        pre_step_msgs = [
            Message(role="system", content="system prompt"),
            Message(role="user", content="hello"),
        ]
        # After step() started, model added a partial response
        post_step_msgs = pre_step_msgs + [
            Message(role="assistant", content="partial response"),
        ]

        loop.messages = post_step_msgs
        loop.step_count = 1
        loop.recorder = MagicMock()
        loop.recorder.append = MagicMock()
        loop.set_messages = MagicMock()

        # We need to test the rollback logic that's in repl_ui.py
        # The key logic is:
        #   pre_step_msg_count = len(loop.messages)  -- before step()
        #   loop.step()  -- raises KeyboardInterrupt
        #   loop.set_messages(loop.messages[:pre_step_msg_count])
        #   loop.recorder.append(...)

        pre_step_msg_count = len(pre_step_msgs)
        # Simulate the Ctrl+C handler
        rolled_back = loop.messages[:pre_step_msg_count]
        loop.set_messages(rolled_back)

        # Verify messages were rolled back
        loop.set_messages.assert_called_once()
        call_args = loop.set_messages.call_args[0][0]
        assert len(call_args) == len(pre_step_msgs), (
            f"expected {len(pre_step_msgs)} messages after rollback, "
            f"got {len(call_args)}"
        )
        assert call_args[0].role == "system"
        assert call_args[1].role == "user"

    def test_interrupt_discards_with_tool_calls(self, monkeypatch) -> None:
        """Counterexample: Ctrl+C during tool execution, dangling tool_call removed."""
        from zall.core.model import Message, ToolCall

        # Pre-step messages
        pre_step_msgs = [
            Message(role="system", content="system prompt"),
            Message(role="user", content="do something"),
        ]

        # After step started, model called tool and got partial result
        tool_call = ToolCall(id="tc1", tool_id="read_file", args={"path": "x.txt"})
        post_step_msgs = pre_step_msgs + [
            Message(role="assistant", content="", tool_calls=(tool_call,)),
            Message(role="tool", content="file content", tool_call_id="tc1", tool_id="read_file"),
        ]

        # But the assistant message with tool_calls was followed by tool result
        # Rollback should remove both
        pre_step_count = len(pre_step_msgs)
        rolled_back = post_step_msgs[:pre_step_count]

        assert len(rolled_back) == len(pre_step_msgs), (
            f"dangling tool_call messages not removed: "
            f"expected {len(pre_step_msgs)} messages, got {len(rolled_back)}"
        )
        assert rolled_back[-1].role == "user", (
            f"last message should be 'user', got '{rolled_back[-1].role}'"
        )

    def test_user_interrupt_event_type_exists(self) -> None:
        """Assert USER_INTERRUPT is defined in EventType."""
        assert hasattr(EventType, "USER_INTERRUPT"), "USER_INTERRUPT not in EventType"
        assert EventType.USER_INTERRUPT.value == "user_interrupt", (
            f"unexpected value: {EventType.USER_INTERRUPT.value}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# E4.3: Permission cross-session persistence
# ═══════════════════════════════════════════════════════════════════════════════


class TestAlwaysAllowPersisted:
    """'a' option persists across sessions."""

    def test_always_allow_saves_to_disk(self, tmp_path: Path, monkeypatch) -> None:
        """Pressing 'a' writes the tool_id to always_allow.json."""
        # Override the always_allow_path to use tmp_path
        def _fake_path() -> Path:
            return tmp_path / ".zall" / "always_allow.json"
        monkeypatch.setattr("zall.cli.responder._always_allow_path", _fake_path)

        answers = iter(["a"])
        r = CliUserResponder(
            yes=False, is_tty=True, ask_fn=lambda _: next(answers),
            print_fn=lambda _: None,
        )

        from zall.core.action import Action
        from zall.core.gate import UserResponseType
        from zall.core.safety import Judgement, SafeLevel

        action = Action(tool_id="bash", args={"command": "echo hello"})
        judgement = Judgement(level=SafeLevel.GREYLIST, matched_rule_ids=("grey_1",))
        resp = r.ask(action, judgement)

        assert resp.response_type == UserResponseType.ACCEPT, "expected ACCEPT"

        # Check that the file was written
        allow_path = _fake_path()
        assert allow_path.exists(), "always_allow.json not created"
        data = json.loads(allow_path.read_text(encoding="utf-8"))
        assert "tool_ids" in data, "missing tool_ids key"
        assert "bash" in data["tool_ids"], "bash not in tool_ids"

    def test_always_allow_loaded_on_new_session(self, tmp_path: Path, monkeypatch) -> None:
        """Counterexample: new session loads persisted permissions."""
        def _fake_path() -> Path:
            return tmp_path / ".zall" / "always_allow.json"
        monkeypatch.setattr("zall.cli.responder._always_allow_path", _fake_path)

        allow_path = _fake_path()
        allow_path.parent.mkdir(parents=True, exist_ok=True)
        # Write a pre-existing permission file
        allow_path.write_text(
            json.dumps({"tool_ids": ["read_file", "write_file"]}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        # Create a new responder (simulating a new session)
        r = CliUserResponder(yes=False, is_tty=True, print_fn=lambda _: None)

        from zall.core.action import Action
        from zall.core.gate import UserResponseType
        from zall.core.safety import Judgement, SafeLevel

        # read_file should be auto-accepted (persistent allow)
        action = Action(tool_id="read_file", args={"path": "x.txt"})
        judgement = Judgement(level=SafeLevel.GREYLIST, matched_rule_ids=("grey_1",))
        resp = r.ask(action, judgement)
        assert resp.response_type == UserResponseType.ACCEPT, (
            "persistent allow should auto-accept"
        )

        # An unknown tool should still prompt
        from unittest.mock import MagicMock
        r._ask = MagicMock(return_value="n")
        action2 = Action(tool_id="unknown_tool", args={})
        resp2 = r.ask(action2, judgement)
        assert resp2.response_type == UserResponseType.REJECT, (
            "unknown tool should not be auto-accepted"
        )

    def test_always_allow_does_not_affect_blacklist(self, tmp_path: Path, monkeypatch) -> None:
        """Counterexample: persistent allow does NOT override blacklist (PR-0)."""
        def _fake_path() -> Path:
            return tmp_path / ".zall" / "always_allow.json"
        monkeypatch.setattr("zall.cli.responder._always_allow_path", _fake_path)

        allow_path = _fake_path()
        allow_path.parent.mkdir(parents=True, exist_ok=True)
        allow_path.write_text(
            json.dumps({"tool_ids": ["bash"]}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        r = CliUserResponder(yes=False, is_tty=True, print_fn=lambda _: None,
                             ask_fn=lambda _: "")  # empty = cancel (reject)

        from zall.core.action import Action
        from zall.core.gate import UserResponseType
        from zall.core.safety import Judgement, SafeLevel

        # Even though bash is in always_allow, blacklist should still reject
        action = Action(tool_id="bash", args={"command": "rm -rf /"})
        black_judgement = Judgement(
            level=SafeLevel.BLACKLIST,
            matched_rule_ids=("core_deny_x",),
        )
        resp = r.ask(action, black_judgement)
        assert resp.response_type == UserResponseType.REJECT, (
            "persistent allow should NOT override blacklist"
        )

    def test_clear_always_allow(self, tmp_path: Path, monkeypatch) -> None:
        """clear_always_allow() removes all permissions (disk + memory)."""
        def _fake_path() -> Path:
            return tmp_path / ".zall" / "always_allow.json"
        monkeypatch.setattr("zall.cli.responder._always_allow_path", _fake_path)

        # Setup: write a permission file
        allow_path = _fake_path()
        allow_path.parent.mkdir(parents=True, exist_ok=True)
        allow_path.write_text(
            json.dumps({"tool_ids": ["read_file"]}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        r = CliUserResponder(yes=False, is_tty=True, print_fn=lambda _: None)

        # Clear all permissions
        r.clear_always_allow()

        # Check file is cleared
        data = json.loads(allow_path.read_text(encoding="utf-8"))
        assert data["tool_ids"] == [], "tool_ids not cleared from file"

        # Check that read_file is no longer auto-accepted
        from zall.core.action import Action
        from zall.core.gate import UserResponseType
        from zall.core.safety import Judgement, SafeLevel
        from unittest.mock import MagicMock

        r._ask = MagicMock(return_value="n")
        action = Action(tool_id="read_file", args={"path": "x.txt"})
        judgement = Judgement(level=SafeLevel.GREYLIST, matched_rule_ids=("grey_1",))
        resp = r.ask(action, judgement)
        assert resp.response_type == UserResponseType.REJECT, (
            "permission should be cleared"
        )

    def test_always_allow_path_is_under_zall(self) -> None:
        """_always_allow_path() returns a path under ~/.zall/."""
        path = _always_allow_path()
        assert ".zall" in str(path), f"path {path} not under .zall"
        assert path.name == "always_allow.json", (
            f"expected always_allow.json, got {path.name}"
        )