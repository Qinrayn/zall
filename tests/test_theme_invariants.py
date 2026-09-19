"""Theme system invariants (G6: single color source, attic 单主题).

IPR-0: each test must contain a counterexample.

Protected invariants:
  I-THEME-1: the theme registry contains exactly one theme — attic
             (真实使用反馈: 多主题没啥用; 回归到多主题必须显式改测试).
  I-THEME-2: _ANSI_MAP is derived (rich Color) and covers every themed style.
  I-THEME-3: switch() rejects unknown names; active_name() falls back to
             default on garbage config/env (self-healing).
  I-THEME-4: attic fills every semantic slot; TUI slots are valid hex.
  I-THEME-6: the default theme is attic (希腊美学) — regressing the
             default to any other theme must fail.
"""

from __future__ import annotations

import re

import pytest

from zall.cli import render, theme


@pytest.fixture(autouse=True)
def _restore_default_theme():
    """Every test leaves the process in the default theme state."""
    yield
    theme.apply(theme.THEMES[theme.DEFAULT_THEME])


# ── I-THEME-1: 单主题注册表 ──


def test_registry_is_single_attic() -> None:
    assert theme.list_themes() == ["attic"]
    assert set(theme.THEMES) == {"attic"}
    assert theme.THEMES["attic"] is theme.ATTIC
    # 反例孪生: 移除的主题不得残留
    assert not hasattr(theme, "OBSIDIAN")
    assert not hasattr(theme, "ANSI")


def test_module_import_applies_active_theme() -> None:
    """render module import must have populated _ANSI_MAP (not empty literal)."""
    assert render._ANSI_MAP, "render module tail did not apply a theme"


# ── I-THEME-2: ANSI derivation ──


def test_ansi_map_covers_all_theme_styles() -> None:
    for t in theme.THEMES.values():
        m = theme.build_ansi_map(t)
        for style in t.all_styles():
            assert style in m, f"{t.name}: no ANSI code for style '{style}'"
            assert m[style].startswith("\033["), f"bad escape for '{style}'"


def test_ansi_code_handles_bold_and_garbage() -> None:
    assert theme.ansi_code("red3 bold").startswith("\033[1;")
    assert theme.ansi_code("") == ""
    assert theme.ansi_code("not-a-color-xyz") == ""  # counterexample: no crash


# ── I-THEME-3: unknown names / fallback ──


def test_switch_rejects_unknown_theme() -> None:
    with pytest.raises(ValueError, match="unknown theme"):
        theme.switch("corinthian")


def test_switch_rejects_removed_themes() -> None:
    """反例: 已移除的 obsidian/ansi 不能再切换。"""
    for removed in ("obsidian", "ansi"):
        with pytest.raises(ValueError, match="unknown theme"):
            theme.switch(removed)


def test_active_name_falls_back_on_garbage_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZALL_THEME", "nonexistent-skin")
    assert theme.active_name() == theme.DEFAULT_THEME


def test_active_name_falls_back_on_removed_theme(monkeypatch: pytest.MonkeyPatch) -> None:
    """曾持久化 obsidian/ansi 的旧配置自动自愈回 attic (不崩)。"""
    monkeypatch.setenv("ZALL_THEME", "obsidian")
    assert theme.active_name() == "attic"


def test_active_name_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZALL_THEME", "attic")
    assert theme.active_name() == "attic"


# ── I-THEME-4: attic 全槽位 ──


def test_attic_fills_every_slot() -> None:
    hex_re = re.compile(r"^#[0-9a-fA-F]{6}$")
    t = theme.ATTIC
    slots = t.repl_slots()
    # MODEL 允许为空 (markdown 自控); 其余槽位必须有值
    for slot, value in slots.items():
        if slot == "MODEL":
            continue
        assert value, f"{t.name}: empty slot {slot}"
    # TUI 槽位必须是合法 hex (Textual 要求)
    for field in ("tui_primary", "tui_accent", "tui_secondary",
                  "tui_background", "tui_surface", "tui_panel",
                  "tui_foreground", "tui_success", "tui_warning", "tui_error"):
        assert hex_re.match(getattr(t, field)), \
            f"{t.name}.{field} is not #rrggbb"


def test_apply_attic_populates_render() -> None:
    theme.apply(theme.ATTIC)
    assert render._C.ACCENT == "#d4af37"          # attic laurel gold (2026-09-19 提对比度)
    assert render.CODE_THEME == "nord"
    assert "#d4af37" in render._ANSI_MAP           # ANSI 表随主题重建
    # 反例孪生: 旧默认 (obsidian gold1) 不得回归
    assert render._C.ACCENT != "gold1"


def test_tui_theme_builder_follows_active(monkeypatch: pytest.MonkeyPatch) -> None:
    """TUI 色板构建器从生效主题取值 (启动时派生)。"""
    pytest.importorskip("textual")
    from zall.cli.tui.app import _build_zall_theme
    monkeypatch.setenv("ZALL_THEME", "attic")
    t = _build_zall_theme()
    assert t.name == "zall"                        # Textual 名固定, CSS 不断裂
    assert t.primary == theme.ATTIC.tui_primary


# ── I-THEME-6: 希腊美学为默认 ──


def test_default_theme_is_attic(monkeypatch: pytest.MonkeyPatch) -> None:
    """默认主题必须是 attic (希腊美学); 反例: 无 env/config 时解析到其它主题即失败。"""
    assert theme.DEFAULT_THEME == "attic"
    monkeypatch.delenv("ZALL_THEME", raising=False)
    monkeypatch.setattr(theme, "_config_theme_name", lambda: "")
    assert theme.active_name() == "attic"
    assert theme.active() is theme.ATTIC


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
