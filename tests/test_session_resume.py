"""会话续接 CLI 参数 + 会话选择器 test (Part D).

covers:
  1. _build_parser: --continue/-C 与 --resume/-r [ID] 解析 (含 -r 无 id 的 const="")
  2. _resolve_resume_target: continue → 最近; -r ID → 该 id; -r 无 id → 选择器; 无会话 → None (优雅退让)
  3. _resume_picker (/resume 无参): 无会话提示; 非交互列表 + usage 提示

IPR-0: 每个 test 含 counterexample。
"""

from __future__ import annotations

import io
from pathlib import Path

import zall.cli.app as app_mod
import zall.cli.commands.session as cmd_session
from zall.cli.app import _build_parser, _resolve_resume_target


def _entries(*names: str):
    """构造 _get_cached_sessions 返回形状: [(Path, meta_dict), ...] (已按新→旧排序)。"""
    return [
        (Path(f"/sessions/{n}"), {"final_state": "MET", "saved_at": "2024-01-01T00:00", "step_count": 3})
        for n in names
    ]


# ──────────────────────────────────────────────────────────────────────────
# _build_parser — 参数解析
# ──────────────────────────────────────────────────────────────────────────


class TestParserResumeFlags:
    def test_continue_long_and_short(self) -> None:
        """Happy path: --continue / -C → continue_=True。"""
        p = _build_parser()
        assert p.parse_args(["--continue"]).continue_ is True
        assert p.parse_args(["-C"]).continue_ is True

    def test_resume_with_id(self) -> None:
        """Happy path: --resume ID / -r ID → resume=ID。"""
        p = _build_parser()
        assert p.parse_args(["--resume", "abc123"]).resume == "abc123"
        assert p.parse_args(["-r", "abc123"]).resume == "abc123"

    def test_resume_without_id_is_empty_const(self) -> None:
        """Happy path: -r 无 id → resume='' (const, 触发选择器); 区别于未给 (None)。"""
        p = _build_parser()
        assert p.parse_args(["-r"]).resume == ""
        assert p.parse_args(["--resume"]).resume == ""

    def test_defaults_when_absent(self) -> None:
        """Counterexample: 不给 → continue_=False, resume=None (不误触发续接)。"""
        p = _build_parser()
        args = p.parse_args([])
        assert args.continue_ is False
        assert args.resume is None


# ──────────────────────────────────────────────────────────────────────────
# _resolve_resume_target — 目标会话解析
# ──────────────────────────────────────────────────────────────────────────


class TestResolveResumeTarget:
    def test_continue_returns_most_recent(self, monkeypatch) -> None:
        """Happy path: --continue → 最近一个会话 (entries[0]) 的名字。"""
        monkeypatch.setattr(
            "zall.cli.session._get_cached_sessions",
            lambda: _entries("newest", "older"),
        )
        args = _build_parser().parse_args(["--continue"])
        assert _resolve_resume_target(args) == "newest"

    def test_continue_no_sessions_returns_none(self, monkeypatch) -> None:
        """Counterexample: --continue 但无会话 → None (优雅退让, 不崩)。"""
        monkeypatch.setattr("zall.cli.session._get_cached_sessions", lambda: [])
        args = _build_parser().parse_args(["--continue"])
        assert _resolve_resume_target(args) is None

    def test_resume_explicit_id_passthrough(self, monkeypatch) -> None:
        """Happy path: -r ID → 直接返回 ID (不查会话列表/不弹选择器)。"""
        # 即使无会话缓存, 显式 id 也应原样返回
        monkeypatch.setattr("zall.cli.session._get_cached_sessions", lambda: [])
        args = _build_parser().parse_args(["-r", "deadbeef"])
        assert _resolve_resume_target(args) == "deadbeef"

    def test_no_flag_returns_none(self) -> None:
        """Counterexample: 无 --continue/-r → None (普通启动, 不续接)。"""
        args = _build_parser().parse_args([])
        assert _resolve_resume_target(args) is None

    def test_resume_no_id_no_sessions_graceful(self, monkeypatch) -> None:
        """Counterexample: -r 无 id 且无会话 → None + 打印提示 (不弹选择器/不崩)。"""
        monkeypatch.setattr("zall.cli.session._get_cached_sessions", lambda: [])
        printed = {}
        monkeypatch.setattr(app_mod.sys, "stderr", io.StringIO())
        args = _build_parser().parse_args(["-r"])
        result = _resolve_resume_target(args)
        assert result is None
        printed["out"] = app_mod.sys.stderr.getvalue()
        assert "no sessions" in printed["out"].lower()

    def test_resume_no_id_with_sessions_uses_picker(self, monkeypatch) -> None:
        """Happy path: -r 无 id 且有会话 → 走 select_prompt 选择器, 返回其选中值。"""
        monkeypatch.setattr(
            "zall.cli.session._get_cached_sessions",
            lambda: _entries("sess_a", "sess_b"),
        )
        # 拦截选择器: 断言它被调用并返回第一个 (最近) 会话名
        captured = {}

        def _fake_select(out, title, choices, **kw):
            captured["choices"] = choices
            return choices[0][0]  # 选第一个的 value (会话名)

        monkeypatch.setattr("zall.cli.select.select_prompt", _fake_select)
        args = _build_parser().parse_args(["-r"])
        assert _resolve_resume_target(args) == "sess_a"
        # 选择器收到的选项 value 应是会话名 (供 _run_resume 用)
        assert captured["choices"][0][0] == "sess_a"


# ──────────────────────────────────────────────────────────────────────────
# _resume_picker — /resume 无参 (斜杠命令)
# ──────────────────────────────────────────────────────────────────────────


class TestResumePicker:
    def test_no_sessions_message(self, monkeypatch) -> None:
        """Counterexample: 无会话 → 提示 '(no sessions to resume)', 不弹选择器。"""
        monkeypatch.setattr(cmd_session, "_get_cached_sessions", lambda: [])
        buf = io.StringIO()
        result = cmd_session._resume_picker(buf, {})
        assert result == "handled"
        assert "no sessions" in buf.getvalue().lower()

    def test_non_interactive_lists_with_usage(self, monkeypatch) -> None:
        """Happy path: 非交互 (无 _input_fn) → 列出会话 + 'usage: /resume <id>' 提示。"""
        monkeypatch.setattr(
            cmd_session, "_get_cached_sessions", lambda: _entries("sA", "sB"),
        )
        buf = io.StringIO()  # 无 isatty → 非交互
        result = cmd_session._resume_picker(buf, {})
        out = buf.getvalue()
        assert result == "handled"
        assert "usage: /resume" in out
        # 至少列出会话短 id (前 8 位)
        assert "sA" in out

    def test_interactive_picker_resumes_choice(self, monkeypatch) -> None:
        """Happy path: 交互 (isatty + _input_fn) → select_prompt 选中 → _run_resume。"""
        monkeypatch.setattr(
            cmd_session, "_get_cached_sessions", lambda: _entries("pick_me", "other"),
        )
        resumed = {}
        monkeypatch.setattr(
            cmd_session, "_run_resume",
            lambda out, sid, state: resumed.setdefault("sid", sid) or "handled",
        )

        class _TTYOut(io.StringIO):
            def isatty(self) -> bool:
                return True

        # select_prompt 走 input_fn='1' → 选第一个 (pick_me)
        state = {"_input_fn": lambda _prompt: "1"}
        result = cmd_session._resume_picker(_TTYOut(), state)
        assert result == "handled"
        assert resumed["sid"] == "pick_me"
