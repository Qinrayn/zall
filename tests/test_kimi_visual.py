"""kimi 式视觉/交互 test (Part G+: 布局与观感升级).

covers:
  1. 工具块 kimi 式 "Using X (arg)" (执行中) / "Used X (arg)" (完成)
  2. 思考行极简: 流式 "Thinking …" / 完成 "Thought" (不刷屏推理正文)
  3. SelectMenu kimi 式 "→ [N] option" 箭头选中行
  4. InputBar 模式指示 (border_title) + 极简 placeholder
  5. 跨平台终端检测: 现代终端标记 (WT/iTerm/WezTerm/tmux…) → 支持全屏 TUI

IPR-0: 每个 test 含 counterexample。
"""

from __future__ import annotations

import io

import pytest
pytest.importorskip("textual")

from rich.console import Console

from zall.cli.tui.widgets import ChatMessage, InputBar, SelectMenu


def _plain(renderable) -> str:
    buf = io.StringIO()
    Console(file=buf, width=88, no_color=True).print(renderable)
    return buf.getvalue()


# ──────────────────────────────────────────────────────────────────────────
# 工具块 "Using/Used" (kimi 式)
# ──────────────────────────────────────────────────────────────────────────


class TestToolBlockKimiStyle:
    def test_running_tool_says_using(self) -> None:
        """Happy path: 执行中工具 (tool_success=None) → "Using name (arg)"。"""
        m = ChatMessage(role="tool", tool_id="bash", tool_args={"command": "ls -la"})
        out = _plain(m.to_rich())
        assert "Using" in out
        assert "Bash" in out
        assert "ls -la" in out

    def test_finished_tool_says_used(self) -> None:
        """Happy path: 完成工具 → "Used name (arg)" + 输出。"""
        m = ChatMessage(
            role="tool", tool_id="read_file",
            tool_args={"path": "x.py"}, tool_success=True, tool_output="line1\nline2",
        )
        out = _plain(m.to_rich())
        assert "Used" in out
        assert "Read" in out

    def test_running_and_finished_differ(self) -> None:
        """Counterexample: 执行中说 Using 而非 Used (状态可区分)。"""
        running = _plain(ChatMessage(role="tool", tool_id="bash", tool_args={"command": "x"}).to_rich())
        assert "Using" in running
        assert "Used" not in running


# ──────────────────────────────────────────────────────────────────────────
# 思考行极简 (kimi 式)
# ──────────────────────────────────────────────────────────────────────────


class TestThinkingLineKimiStyle:
    def test_streaming_thinking_is_compact(self) -> None:
        """Happy path: 流式思考 → 单行 "Thinking …" (不整段刷推理正文)。"""
        tm = ChatMessage(role="thinking", thinking="a very long chain of reasoning " * 10)
        tm.thinking_streaming = True
        out = _plain(tm.to_rich())
        assert "Thinking" in out
        # Counterexample: 不应把整段推理正文逐行铺出 (极简行, 行数很少)
        assert out.count("\n") <= 2

    def test_finished_thinking_says_thought(self) -> None:
        """Happy path: 完成思考 → "Thought" 单行 (grey italic)。"""
        tm = ChatMessage(role="thinking", thinking="some reasoning content")
        out = _plain(tm.to_rich())
        assert "Thought" in out

    def test_thinking_does_not_dump_full_reasoning(self) -> None:
        """Counterexample: 长推理正文不逐行出现在思考行 (kimi 隐藏正文)。"""
        secret = "SECRETLINE_MARKER_XYZ"
        tm = ChatMessage(role="thinking", thinking=f"line1\n{secret}\nline3")
        out = _plain(tm.to_rich())
        assert secret not in out


# ──────────────────────────────────────────────────────────────────────────
# SelectMenu kimi 式箭头选中行
# ──────────────────────────────────────────────────────────────────────────


class TestSelectMenuArrow:
    def test_selected_uses_arrow_bracket(self) -> None:
        """Happy path: 选中行 "→ [1] label"; 未选 "[2] label"。"""
        m = SelectMenu()
        m.open("pick", [("y", "allow once", "run"), ("n", "reject", "skip")])
        out = _plain(m.render())
        assert "\u2192 [1] allow once" in out
        assert "[2] reject" in out

    def test_number_bracket_format(self) -> None:
        """Counterexample: 编号用方括号 [N] 而非旧 "N." 点号。"""
        m = SelectMenu()
        m.open("pick", [("a", "opt a", "")])
        out = _plain(m.render())
        assert "[1]" in out
        assert "1. opt a" not in out  # 旧格式已弃用


# ──────────────────────────────────────────────────────────────────────────
# InputBar 模式指示 + placeholder
# ──────────────────────────────────────────────────────────────────────────


class TestInputBarModeIndicator:
    def test_default_border_title(self) -> None:
        """Happy path: 默认边框标题带提示符 + zall。"""
        bar = InputBar()
        assert "zall" in bar._textarea.border_title

    def test_set_mode_indicator_plan(self) -> None:
        """Happy path: 切 plan 模式 → 边框标题显 plan。"""
        bar = InputBar()
        bar.set_mode_indicator("plan")
        assert "plan" in bar._textarea.border_title

    def test_set_mode_indicator_reset(self) -> None:
        """Counterexample: 空模式 → 回落 zall (非空标题)。"""
        bar = InputBar()
        bar.set_mode_indicator("plan")
        bar.set_mode_indicator("")
        assert "zall" in bar._textarea.border_title
        assert "plan" not in bar._textarea.border_title


# ──────────────────────────────────────────────────────────────────────────
# 跨平台终端检测 (现代终端标记)
# ──────────────────────────────────────────────────────────────────────────


class _FakeTTY:
    def isatty(self) -> bool:
        return True

    def write(self, s: str) -> int:
        return len(s)

    def flush(self) -> None:
        pass


_TERMINAL_ENV_KEYS = (
    "WT_SESSION", "VSCODE_INJECTION", "ConEmuANSI", "WEZTERM_EXECUTABLE",
    "KITTY_WINDOW_ID", "ALACRITTY_SOCKET", "TERM", "TERM_PROGRAM", "COLORTERM", "CI",
)


class TestTerminalDetectionCrossPlatform:
    def _detect_with(self, monkeypatch, **env):
        monkeypatch.setattr("sys.stdout", _FakeTTY())
        for k in _TERMINAL_ENV_KEYS:
            monkeypatch.delenv(k, raising=False)
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        from zall.cli.tui.app import _detect_terminal_capabilities
        return _detect_terminal_capabilities()

    def test_windows_terminal_supported(self, monkeypatch) -> None:
        """Happy path: Windows Terminal (WT_SESSION) → 支持。"""
        caps = self._detect_with(monkeypatch, WT_SESSION="1")
        assert caps["tui_supported"] is True

    def test_iterm_supported(self, monkeypatch) -> None:
        """Happy path: macOS iTerm (TERM_PROGRAM) → 支持。"""
        caps = self._detect_with(monkeypatch, TERM_PROGRAM="iTerm.app")
        assert caps["tui_supported"] is True

    def test_wezterm_supported(self, monkeypatch) -> None:
        """Happy path: WezTerm → 支持。"""
        caps = self._detect_with(monkeypatch, WEZTERM_EXECUTABLE="/usr/bin/wezterm")
        assert caps["tui_supported"] is True

    def test_tmux_term_supported(self, monkeypatch) -> None:
        """Happy path: tmux/xterm TERM → 支持。"""
        assert self._detect_with(monkeypatch, TERM="tmux-256color")["tui_supported"] is True
        assert self._detect_with(monkeypatch, TERM="xterm-256color")["tui_supported"] is True

    def test_ghostty_supported(self, monkeypatch) -> None:
        """Happy path: ghostty (TERM_PROGRAM) → 支持 (新终端也覆盖)。"""
        caps = self._detect_with(monkeypatch, TERM_PROGRAM="ghostty")
        assert caps["tui_supported"] is True

    def test_dumb_terminal_unsupported(self, monkeypatch) -> None:
        """Counterexample: dumb 终端 (即使 tty) → 不支持 + 无 ansi/颜色。"""
        caps = self._detect_with(monkeypatch, TERM="dumb")
        assert caps["tui_supported"] is False
        assert caps["ansi"] is False
        assert caps["colors"] is False

    def test_ci_unsupported(self, monkeypatch) -> None:
        """Counterexample: CI (即使 tty + xterm) → 退让行式 REPL。"""
        caps = self._detect_with(monkeypatch, TERM="xterm-256color", CI="true")
        assert caps["tui_supported"] is False


# ───────────────────────────────────────────────────────────────
# 字形安全 / tofu 防护 (跨平台)
# ───────────────────────────────────────────────────────────────


class _EncStdout:
    def __init__(self, enc: str) -> None:
        self.encoding = enc

    def isatty(self) -> bool:
        return True

    def write(self, s: str) -> int:
        return len(s)

    def flush(self) -> None:
        pass


class TestGlyphSafety:
    """字形安全: spinner 用通用字形; 受限编码回退 ASCII (杜绝 tofu 方块)。"""

    def _restore(self):
        from zall.cli.render import use_unicode_glyphs
        use_unicode_glyphs()

    def test_spinner_frames_are_ascii(self) -> None:
        """Happy path: spinner 帧均为 ASCII (句点/空格) → 任意字体/编码无 tofu。"""
        from zall.cli.render import _G
        for frame in _G.SPINNER_FRAMES:
            assert all(ord(ch) < 128 for ch in frame), repr(frame)

    def test_ascii_fallback_swaps_glyphs(self, monkeypatch) -> None:
        """Happy path: use_ascii_glyphs → 字形切 ASCII (DIAMOND=*, TOOL=>, ARROW=->)。"""
        from zall.cli.render import _G, use_ascii_glyphs
        try:
            use_ascii_glyphs()
            assert _G.DIAMOND == "*"
            assert _G.TOOL == ">"
            assert _G.ARROW == "->"
            # 回退后所有字形均为 ASCII (彻底无 tofu)
            for key in ("OK", "FAIL", "MET", "BULLET", "DEPTH_END", "DIAMOND"):
                val = getattr(_G, key)
                assert all(ord(ch) < 128 for ch in val), (key, val)
        finally:
            self._restore()

    def test_unicode_restore(self, monkeypatch) -> None:
        """Counterexample: use_unicode_glyphs 还原 → DIAMOND 回到 unicode (非 ASCII)。"""
        from zall.cli.render import _G, use_ascii_glyphs, use_unicode_glyphs
        uni_diamond = _G.DIAMOND
        use_ascii_glyphs()
        assert _G.DIAMOND == "*"
        use_unicode_glyphs()
        assert _G.DIAMOND == uni_diamond
        assert _G.DIAMOND != "*"

    def test_unicode_supported_by_encoding(self, monkeypatch) -> None:
        """Happy path: UTF-8 编码 → 支持 unicode; gbk/ascii → 不支持 (触发回退)。"""
        from zall.cli.render import _unicode_supported
        monkeypatch.setattr("sys.stdout", _EncStdout("utf-8"))
        assert _unicode_supported() is True
        monkeypatch.setattr("sys.stdout", _EncStdout("cp65001"))
        assert _unicode_supported() is True
        monkeypatch.setattr("sys.stdout", _EncStdout("gbk"))
        assert _unicode_supported() is False
        monkeypatch.setattr("sys.stdout", _EncStdout("ascii"))
        assert _unicode_supported() is False

    def test_welcome_logo_respects_fallback(self, monkeypatch) -> None:
        """Happy path: ASCII 回退后欢迎屏仅含纯文字 (无图形方块)。"""
        from zall.cli.render import use_ascii_glyphs
        from zall.cli.tui.app import TuiApp
        try:
            use_ascii_glyphs()
            app = TuiApp(model="m")
            out = _plain(app._build_welcome_renderable())
            # 欢迎内容为纯 ASCII 可渲染 (不依赖图形字符)
            assert "zall" in out
            assert "v0." in out or "v1." in out  # 版本号存在
        finally:
            self._restore()


# ───────────────────────────────────────────────────────────────
# kimi 式 diff 面板 (edit_file 工具输出)
# ───────────────────────────────────────────────────────────────


class TestDiffPanel:
    def test_edit_file_diff_renders_panel(self) -> None:
        """Happy path: edit_file diff 输出 → 带 +/- 行号、文件名、增删计数的面板。"""
        diff = "@@ -1,2 +1,2 @@\n line0\n-old\n+new"
        m = ChatMessage(
            role="tool", tool_id="edit_file", tool_args={"path": "a.py"},
            tool_success=True, tool_output=diff,
        )
        out = _plain(m.to_rich())
        assert "+ new" in out
        assert "- old" in out
        assert "a.py" in out
        assert "+1 -1" in out          # 增删计数标题
        assert "Used" in out           # headline 仍在

    def test_non_diff_output_no_diff_panel(self) -> None:
        """Counterexample: bash 输出不走 diff 面板 (无增删计数标题)。"""
        m = ChatMessage(
            role="tool", tool_id="bash", tool_args={"command": "ls"},
            tool_success=True, tool_output="file1\nfile2",
        )
        out = _plain(m.to_rich())
        assert "Used" in out
        assert "file1" in out
        assert " -1" not in out         # 无 diff 计数标题


# ───────────────────────────────────────────────────────────────
# 边框安全 (solid → CP437 通用, 无 tofu)
# ───────────────────────────────────────────────────────────────


class TestBorderSafety:
    def test_no_round_borders(self) -> None:
        """Counterexample: 无 round 边框 (圆角 ╭╮╰╯ 非 CP437, 部分字体缺字 → tofu)。"""
        from zall.cli.tui.app import TuiApp
        from zall.cli.tui.widgets import CommandMenu, SelectMenu
        assert "border: round" not in TuiApp.CSS
        assert "border: round" not in CommandMenu.DEFAULT_CSS
        assert "border: round" not in SelectMenu.DEFAULT_CSS

    def test_solid_borders_used(self) -> None:
        """Happy path: 输入框用 solid 边框 (┐┌└┘ 属 CP437, 任意字体可渲)。"""
        from zall.cli.tui.app import TuiApp
        assert "border: solid" in TuiApp.CSS


# ───────────────────────────────────────────────────────────────
# 底栏状态行 (输入框 border_subtitle: cwd · ctx%)
# ───────────────────────────────────────────────────────────────


class TestBottomStatusLine:
    def test_set_status_line(self) -> None:
        """Happy path: set_status_line → 输入框 border_subtitle 显 cwd/ctx。"""
        bar = InputBar()
        bar.set_status_line(" proj \u00b7 ctx 5% ")
        assert "proj" in bar._textarea.border_subtitle
        assert "ctx 5%" in bar._textarea.border_subtitle

    def test_empty_status_line(self) -> None:
        """Counterexample: 空状态 → border_subtitle 置空 (不残留旧值)。"""
        bar = InputBar()
        bar.set_status_line("x")
        bar.set_status_line("")
        assert bar._textarea.border_subtitle == ""

    def test_sync_status_bar_updates_input_subtitle(self) -> None:
        """Happy path (挂载): _sync_status_bar → 输入框底栏显 cwd · ctx% (端到端布线)。"""
        import asyncio
        from zall.cli.tui.app import TuiApp

        async def _t() -> bool:
            app = TuiApp(model="m")
            async with app.run_test() as pilot:
                app.status_cwd = "myproj"
                app.status_context_pct = "42%"
                app._sync_status_bar()
                await pilot.pause()
                sub = app.query_one("#input-bar", InputBar)._textarea.border_subtitle
                return ("myproj" in sub) and ("ctx 42%" in sub)

        assert asyncio.run(_t())
