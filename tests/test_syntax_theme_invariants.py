"""G7 ANSI-16 语法主题不变量测试 (IPR-0, 含反例).

不变量:
  I-ANSI16-1  resolve_code_theme: "zall-ansi" (大小写不敏感) → ANSISyntaxTheme
              实例; 其余名字原样透传 (反例)
  I-ANSI16-2  resolve_code_bg: 空串 → None (跟随终端背景); 非空原样 (反例)
  I-ANSI16-3  ANSI 纯净性: zall-ansi 主题渲染代码块只发 16 色 SGR,
              绝无 truecolor (38;2;) 前景转义 (反例: one-dark 有 truecolor)
  I-ANSI16-4  ansi 主题注册完整: THEMES/switch/list_themes 可用,
              apply 后 render.CODE_THEME/CODE_BG 正确, 全槽位可 ANSI 派生
"""

from __future__ import annotations

import io

import pytest
from rich.console import Console
from rich.markdown import Markdown
from rich.syntax import ANSISyntaxTheme, Syntax

from zall.cli.syntax_theme import (
    ANSI_THEME_NAME,
    ZALL_ANSI_THEME,
    resolve_code_bg,
    resolve_code_theme,
)

CODE = "def f(x):\n    # comment\n    return 'str' + x\n"


def _ansi_render(renderable, width: int = 60) -> str:
    buf = io.StringIO()
    console = Console(
        file=buf, force_terminal=True, color_system="truecolor", width=width
    )
    console.print(renderable)
    return buf.getvalue()


# ── I-ANSI16-1 resolve_code_theme ──


def test_resolve_returns_instance_case_insensitive():
    assert resolve_code_theme("zall-ansi") is ZALL_ANSI_THEME
    assert resolve_code_theme("ZALL-ANSI") is ZALL_ANSI_THEME
    assert resolve_code_theme(ANSI_THEME_NAME) is ZALL_ANSI_THEME


def test_resolve_passthrough_counterexample():
    """反例: pygments 具名主题与实例原样透传。"""
    assert resolve_code_theme("one-dark") == "one-dark"
    assert resolve_code_theme("nord") == "nord"
    other = ANSISyntaxTheme({})
    assert resolve_code_theme(other) is other


# ── I-ANSI16-2 resolve_code_bg ──


def test_resolve_bg_empty_is_none():
    assert resolve_code_bg("") is None


def test_resolve_bg_passthrough_counterexample():
    assert resolve_code_bg("#1e1e1e") == "#1e1e1e"


# ── I-ANSI16-3 ANSI 纯净性 ──


def test_syntax_emits_no_truecolor():
    out = _ansi_render(
        Syntax(CODE, "python", theme=resolve_code_theme("zall-ansi"),
               background_color=resolve_code_bg(""))
    )
    assert "\x1b[38;2;" not in out, "zall-ansi must not emit truecolor SGR"
    assert "\x1b[" in out  # 确实有着色


def test_markdown_code_block_emits_no_truecolor():
    md = Markdown(
        f"```python\n{CODE}```\n", code_theme=resolve_code_theme("zall-ansi")
    )
    out = _ansi_render(md)
    assert "\x1b[38;2;" not in out
    assert "return" in out  # 代码内容确实渲染出来了


def test_truecolor_theme_counterexample():
    """反例: one-dark (truecolor 固定色板) 会发 38;2; 转义。"""
    out = _ansi_render(Syntax(CODE, "python", theme="one-dark"))
    assert "\x1b[38;2;" in out


# ── I-ANSI16-4 ansi 主题注册完整 ──


@pytest.fixture()
def _restore_theme():
    yield
    from zall.cli import theme

    theme.apply(theme.OBSIDIAN)


def test_ansi_theme_registered(_restore_theme):
    from zall.cli import render, theme

    assert "ansi" in theme.THEMES
    assert "ansi" in theme.list_themes()
    t = theme.switch("ansi")
    assert t.name == "ansi"
    assert render.CODE_THEME == ANSI_THEME_NAME
    assert render.CODE_BG == ""
    assert resolve_code_bg(render.CODE_BG) is None
    assert theme.current().name == "ansi"


def test_ansi_theme_all_slots_derivable(_restore_theme):
    """ansi 主题每个非空色槽都能派生 ANSI 前缀 (16 色语义名)。"""
    from dataclasses import fields as dc_fields

    from zall.cli import theme

    t = theme.THEMES["ansi"]
    for f in dc_fields(t):
        val = getattr(t, f.name)
        if not isinstance(val, str) or not val or f.name in (
            "name", "code_theme", "code_bg",
        ) or f.name.startswith(("tui_", "diff_")):
            continue
        prefix = theme.ansi_code(val)
        assert prefix.startswith("\x1b["), f"slot {f.name}={val!r} not derivable"
        assert ";2;" not in prefix, f"slot {f.name}={val!r} derived truecolor"


def test_unknown_theme_counterexample():
    from zall.cli import theme

    with pytest.raises(ValueError):
        theme.switch("parthenon")
