"""TuiApp 主题 + 无硬编码颜色 不变量 (TUI 观感重塑, Claude/Pi 式).

把散落的字面 hex 归一到单一 zall 主题 (调色板真源)。这些测试守护:
颜色不再散落在 CSS 里, 且主题已注册并激活。

不变量 (each with counterexample):
  A  TuiApp.CSS 与各 DEFAULT_CSS **无字面 hex** (反例: 出现 #rrggbb 即失败)。
  B  zall 主题已注册且为当前主题。
  C  _ZALL_THEME (调色板真源) 存在且关键色为合法 hex。
"""

from __future__ import annotations

import re

import pytest
pytest.importorskip("textual")

_HEX = re.compile(r"#[0-9a-fA-F]{6}\b")


# ══════════════════════════════════════════════════════════════════
# A: CSS 无字面 hex (颜色单一真源)
# ══════════════════════════════════════════════════════════════════
class TestNoHardcodedHexInCSS:
    def test_app_css_has_no_hex(self) -> None:
        from zall.cli.tui.app import TuiApp
        assert _HEX.findall(TuiApp.CSS) == []          # A 反例: 字面 hex 即失败

    def test_widget_css_has_no_hex(self) -> None:
        from zall.cli.tui.widgets import CommandMenu, StatusBar
        assert _HEX.findall(CommandMenu.DEFAULT_CSS) == []
        assert _HEX.findall(StatusBar.DEFAULT_CSS) == []


# ══════════════════════════════════════════════════════════════════
# B/C: zall 主题注册 + 激活 + 调色板真源
# ══════════════════════════════════════════════════════════════════
class TestZallTheme:
    def test_registered_and_active(self) -> None:
        from zall.cli.tui.app import TuiApp
        app = TuiApp()
        assert app.theme == "zall"                     # B
        assert "zall" in app.available_themes

    def test_palette_is_source_of_truth(self) -> None:
        from zall.cli.tui.app import _ZALL_THEME
        assert _ZALL_THEME.name == "zall"              # C
        for c in (_ZALL_THEME.primary, _ZALL_THEME.accent, _ZALL_THEME.background,
                  _ZALL_THEME.surface, _ZALL_THEME.panel, _ZALL_THEME.foreground):
            assert re.fullmatch(r"#[0-9a-fA-F]{6}", c), f"invalid theme color {c!r}"
