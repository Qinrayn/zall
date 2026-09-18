"""Interaction polish tests — multiline input, recovery prompt, error messages.

Covers:
  1. Multiline backslash continuation (\\ -> join next line)
  2. Multiline paste detection (embedded \\n accepted directly)
  3. Single-line input unchanged (counterexample)
  4. Autosave recovery shows message count and last content summary
  5. HTTP 422 error message contains specific tool schema hint
  6. HTTP 401 error message contains API key hint

IPR-0: each test contains a counterexample assertion.
"""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from zall.adapters.base import BaseAdapter, _ERROR_MAP
from zall.cli.repl_ui import _read_multiline_input


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Multiline backslash continuation
# ═══════════════════════════════════════════════════════════════════════════════


class TestMultilineBackslashContinuation:
    """Line ending with \\ continues to next line with ... prompt."""

    def test_backslash_joins_next_line(self) -> None:
        """Happy path: line ending with \\ joins the next line."""
        inputs = iter(["hello \\", "world"])
        result = _read_multiline_input("> ", lambda _: next(inputs))
        # The space before the backslash is preserved (rstrip only removes
        # trailing whitespace, backslash is not whitespace)
        assert result == "hello world", (
            f"expected 'hello world', got '{result}'"
        )

    def test_backslash_multiple_continuations(self) -> None:
        """Happy path: multiple \\ continuations join all lines."""
        inputs = iter(["a \\", "b \\", "c"])
        result = _read_multiline_input("> ", lambda _: next(inputs))
        assert result == "a b c", (
            f"expected 'a b c', got '{result}'"
        )

    def test_backslash_with_trailing_whitespace(self) -> None:
        """Counterexample: line ending with '\\  ' (trailing spaces) strips them."""
        inputs = iter(["hello \\  ", "world"])
        result = _read_multiline_input("> ", lambda _: next(inputs))
        # The trailing spaces after the backslash are stripped by rstrip(),
        # but the space before the backslash is preserved
        assert result == "hello world", (
            f"expected 'hello world', got '{result}'"
        )

    def test_backslash_continuation_prompt_no_space_added(self) -> None:
        """Counterexample: continuation does not insert extra spaces between parts."""
        # "print(" + "x)" should produce "print(x)" not "print( x)"
        inputs = iter(["print(\\", "x)"])
        result = _read_multiline_input("> ", lambda _: next(inputs))
        assert result == "print(x)", (
            f"expected 'print(x)', got '{result}'"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Multiline paste detection
# ═══════════════════════════════════════════════════════════════════════════════


class TestMultilinePasteDetection:
    """Input containing \\n (paste from clipboard) is accepted directly."""

    def test_paste_with_newlines_accepted_directly(self) -> None:
        """Happy path: pasted multi-line input is returned as-is."""
        pasted = "line1\nline2\nline3"
        result = _read_multiline_input("> ", lambda _: pasted)
        assert result == pasted, (
            f"expected pasted content unchanged, got '{result}'"
        )

    def test_paste_with_carriage_return_newline(self) -> None:
        """Counterexample: pasted content with \\r\\n is also accepted directly."""
        pasted = "line1\r\nline2\r\nline3"
        result = _read_multiline_input("> ", lambda _: pasted)
        assert result == pasted, (
            f"expected pasted content with CRLF unchanged, got '{result}'"
        )

    def test_paste_single_line_no_newline(self) -> None:
        """Happy path: single-line paste without \\n is treated as normal input."""
        pasted = "hello world"
        result = _read_multiline_input("> ", lambda _: pasted)
        assert result == pasted, (
            f"expected single-line input unchanged, got '{result}'"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Single-line input unchanged (counterexample)
# ═══════════════════════════════════════════════════════════════════════════════


class TestMultilineSingleLineNoChange:
    """Single-line input behavior is unchanged (identity)."""

    def test_single_line_no_backslash(self) -> None:
        """Happy path: normal single-line input passes through unchanged."""
        result = _read_multiline_input("> ", lambda _: "hello world")
        assert result == "hello world", (
            f"expected 'hello world', got '{result}'"
        )

    def test_single_line_no_newline(self) -> None:
        """Counterexample: single line without \\n is not treated as paste."""
        line = "def hello(): pass"
        result = _read_multiline_input("> ", lambda _: line)
        assert result == line, (
            f"expected single line unchanged, got '{result}'"
        )
        # Verify it does NOT contain a newline it didn't have
        assert "\n" not in result, (
            "single-line input should not have newlines added"
        )

    def test_single_line_empty_string(self) -> None:
        """Empty input is returned as-is (empty string)."""
        result = _read_multiline_input("> ", lambda _: "")
        assert result == "", (
            f"expected empty string, got '{result}'"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Autosave recovery shows count and last content summary
# ═══════════════════════════════════════════════════════════════════════════════


class TestAutosaveRecoveryShowsSummary:
    """Recovery prompt shows message count, time, and last message summary."""

    def test_recovery_shows_message_count(self, tmp_path: Path, monkeypatch) -> None:
        """Happy path: recovery prompt contains message count."""
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
            "pid": 99999999,
        }
        autosave_path.parent.mkdir(parents=True, exist_ok=True)
        autosave_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

        out = MagicMock()
        out.isatty.return_value = True
        out.write = MagicMock()

        # We need to check what gets written to out before the ask prompt
        # _check_repl_autosave writes to out before asking
        state = {"_input_fn": lambda _: "n"}
        session_mod._check_repl_autosave(out, state)

        # Collect all written content
        written = ""
        for call_args in out.write.call_args_list:
            args, _ = call_args
            written += args[0]

        assert "2 messages" in written, (
            f"expected '2 messages' in recovery prompt, got: {written}"
        )

    def test_recovery_shows_last_content_summary(self, tmp_path: Path, monkeypatch) -> None:
        """Happy path: recovery prompt contains last message content summary."""
        from zall.cli import session as session_mod
        autosave_path = tmp_path / ".zall" / ".repl_autosave.json"
        monkeypatch.setattr(session_mod, "_REPL_AUTOSAVE", autosave_path)

        long_content = "This is a very long message that should be summarized in the recovery prompt with only the first 50 characters shown"
        data = {
            "model": "test-model",
            "verbose": False,
            "usage": {"prompt": 10, "completion": 20},
            "messages": [
                {"role": "user", "content": "short", "tool_call_id": None, "tool_calls": []},
                {"role": "assistant", "content": long_content, "tool_call_id": None, "tool_calls": []},
            ],
            "saved_at": "2026-07-19T10:00:00",
            "pid": 99999998,
        }
        autosave_path.parent.mkdir(parents=True, exist_ok=True)
        autosave_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

        out = MagicMock()
        out.isatty.return_value = True
        out.write = MagicMock()

        state = {"_input_fn": lambda _: "n"}
        session_mod._check_repl_autosave(out, state)

        written = ""
        for call_args in out.write.call_args_list:
            args, _ = call_args
            written += args[0]

        # Should contain the last role and a truncated version of the content
        assert "last:" in written, (
            f"expected 'last:' in recovery prompt, got: {written}"
        )
        assert "[assistant]" in written or "assistant" in written, (
            f"expected last role in recovery prompt, got: {written}"
        )
        # The long content should be truncated to 50 chars with "..."
        # "This is a very long message that should be summari" is 50 chars
        assert "This is a very long message that should be summari" in written, (
            f"expected first 50 chars of last message in recovery prompt, got: {written}"
        )

    def test_recovery_shows_time(self, tmp_path: Path, monkeypatch) -> None:
        """Happy path: recovery prompt contains saved time."""
        from zall.cli import session as session_mod
        autosave_path = tmp_path / ".zall" / ".repl_autosave.json"
        monkeypatch.setattr(session_mod, "_REPL_AUTOSAVE", autosave_path)

        data = {
            "model": "test-model",
            "verbose": False,
            "usage": {"prompt": 10, "completion": 20},
            "messages": [
                {"role": "user", "content": "hello", "tool_call_id": None, "tool_calls": []},
            ],
            "saved_at": "2026-07-19T10:00:00",
            "pid": 99999997,
        }
        autosave_path.parent.mkdir(parents=True, exist_ok=True)
        autosave_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

        out = MagicMock()
        out.isatty.return_value = True
        out.write = MagicMock()

        state = {"_input_fn": lambda _: "n"}
        session_mod._check_repl_autosave(out, state)

        written = ""
        for call_args in out.write.call_args_list:
            args, _ = call_args
            written += args[0]

        assert "2026-07-19" in written, (
            f"expected saved time in recovery prompt, got: {written}"
        )

    def test_typed_task_during_restore_prompt_is_not_swallowed(
            self, tmp_path: Path, monkeypatch) -> None:
        """2026-09-18 实测: 启动恢复提示弹出时用户已在打首条任务, 整行被
        ask() 吃掉后静默丢弃, 会话空等。修复: 非 y/N 长答案转交 REPL
        作为 _pending_first_input, 不再吞输入。"""
        from zall.cli import session as session_mod
        autosave_path = tmp_path / ".zall" / ".repl_autosave.json"
        monkeypatch.setattr(session_mod, "_REPL_AUTOSAVE", autosave_path)

        data = {
            "model": "test-model",
            "verbose": False,
            "usage": {"prompt": 10, "completion": 20},
            "messages": [
                {"role": "user", "content": "hello", "tool_call_id": None, "tool_calls": []},
            ],
            "saved_at": "2026-09-18T10:00:00",
            "pid": 99999996,
        }
        autosave_path.parent.mkdir(parents=True, exist_ok=True)
        autosave_path.write_text(json.dumps(data), encoding="utf-8")

        out = MagicMock()
        out.isatty.return_value = True
        task = "用一句话介绍你自己"
        state = {"_input_fn": lambda _: task}
        result = session_mod._check_repl_autosave(out, state)

        assert result is False  # 恢复被拒绝
        assert state.get("_pending_first_input") == task
        assert not autosave_path.exists()  # 旧自动存档仍被清理

    def test_explicit_no_does_not_create_pending_input(
            self, tmp_path: Path, monkeypatch) -> None:
        """Counterexample: 显式 n/no/空 不产生 pending 输入。"""
        from zall.cli import session as session_mod
        for answer in ("n", "no", ""):
            autosave_path = tmp_path / ".zall" / ".repl_autosave.json"
            monkeypatch.setattr(session_mod, "_REPL_AUTOSAVE", autosave_path)
            data = {
                "model": "test-model",
                "verbose": False,
                "usage": {"prompt": 0, "completion": 0},
                "messages": [
                    {"role": "user", "content": "hi", "tool_call_id": None, "tool_calls": []},
                ],
                "saved_at": "2026-09-18T10:00:00",
                "pid": 99999995,
            }
            autosave_path.parent.mkdir(parents=True, exist_ok=True)
            autosave_path.write_text(json.dumps(data), encoding="utf-8")

            out = MagicMock()
            out.isatty.return_value = True
            state = {"_input_fn": lambda _: answer}
            session_mod._check_repl_autosave(out, state)
            assert "_pending_first_input" not in state, (
                f"answer {answer!r} should not become pending input"
            )
            assert not autosave_path.exists()


# ═══════════════════════════════════════════════════════════════════════════════
# 5. HTTP 422 error message contains specific tool schema hint
# ═══════════════════════════════════════════════════════════════════════════════


class TestErrorMessage422:
    """422 error shows specific tool schema hint."""

    def test_422_in_error_map(self) -> None:
        """422 is defined in _ERROR_MAP."""
        assert 422 in _ERROR_MAP, "422 not in _ERROR_MAP"
        msg = _ERROR_MAP[422]
        assert "tool schema" in msg.lower(), (
            f"422 message should mention tool schema, got: {msg}"
        )

    def test_422_make_error_response(self) -> None:
        """Happy path: make_error_response(422) returns user-friendly message."""
        adapter = BaseAdapter.__new__(BaseAdapter)
        adapter._api_key = "test"
        adapter._api_base = "https://api.test.com"
        adapter._model = "test-model"
        adapter._timeout = 120.0

        resp = adapter.make_error_response(422, '{"error": "invalid"}')
        assert "tool schema" in resp.content.lower(), (
            f"expected 'tool schema' hint in 422 response, got: {resp.content}"
        )

    def test_422_not_confused_with_other_errors(self) -> None:
        """Counterexample: 422 message differs from 401/429 messages."""
        msg_422 = _ERROR_MAP.get(422, "")
        msg_401 = _ERROR_MAP.get(401, "")
        msg_429 = _ERROR_MAP.get(429, "")
        assert msg_422 != msg_401, "422 and 401 messages should differ"
        assert msg_422 != msg_429, "422 and 429 messages should differ"


# ═══════════════════════════════════════════════════════════════════════════════
# 6. HTTP 401 error message contains API key hint
# ═══════════════════════════════════════════════════════════════════════════════


class TestErrorMessage401:
    """401 error shows API key hint."""

    def test_401_in_error_map(self) -> None:
        """401 is defined in _ERROR_MAP."""
        assert 401 in _ERROR_MAP, "401 not in _ERROR_MAP"
        msg = _ERROR_MAP[401]
        assert "API key" in msg, (
            f"401 message should mention 'API key', got: {msg}"
        )

    def test_401_make_error_response(self) -> None:
        """Happy path: make_error_response(401) returns user-friendly message."""
        adapter = BaseAdapter.__new__(BaseAdapter)
        adapter._api_key = "test"
        adapter._api_base = "https://api.test.com"
        adapter._model = "test-model"
        adapter._timeout = 120.0

        resp = adapter.make_error_response(401, '{"error": "unauthorized"}')
        assert "API key" in resp.content, (
            f"expected 'API key' hint in 401 response, got: {resp.content}"
        )

    def test_401_not_confused_with_404(self) -> None:
        """Counterexample: 401 message differs from 404."""
        msg_401 = _ERROR_MAP.get(401, "")
        msg_404 = _ERROR_MAP.get(404, "")
        assert msg_401 != msg_404, "401 and 404 messages should differ"


# ═══════════════════════════════════════════════════════════════════════════════
# 7. Bonus: HTTP 429 rate limit message
# ═══════════════════════════════════════════════════════════════════════════════


class TestErrorMessage429:
    """429 error shows rate limit hint."""

    def test_429_in_error_map(self) -> None:
        """429 is defined in _ERROR_MAP."""
        assert 429 in _ERROR_MAP, "429 not in _ERROR_MAP"
        msg = _ERROR_MAP[429]
        assert "rate limit" in msg.lower(), (
            f"429 message should mention 'rate limit', got: {msg}"
        )

    def test_429_make_error_response(self) -> None:
        """Happy path: make_error_response(429) is user-friendly."""
        adapter = BaseAdapter.__new__(BaseAdapter)
        adapter._api_key = "test"
        adapter._api_base = "https://api.test.com"
        adapter._model = "test-model"
        adapter._timeout = 120.0

        resp = adapter.make_error_response(429, '{"error": "too many requests"}')
        assert "rate limit" in resp.content.lower(), (
            f"expected 'rate limit' in 429 response, got: {resp.content}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 8. Re-export verification for _read_multiline_input
# ═══════════════════════════════════════════════════════════════════════════════


class TestReadMultilineInputExported:
    """_read_multiline_input is importable from repl_ui."""

    def test_function_exists(self) -> None:
        """_read_multiline_input is defined and callable."""
        assert callable(_read_multiline_input), (
            "_read_multiline_input should be callable"
        )

    def test_returns_none_on_none_input(self) -> None:
        """Counterexample: None input returns None (not empty string)."""
        result = _read_multiline_input("> ", lambda _: None)
        assert result is None, (
            f"expected None for None input, got '{result}'"
        )