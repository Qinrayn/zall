"""Theme system invariants (G6: single color source, attic default + obsidian).

IPR-0: each test must contain a counterexample.

Protected invariants:
  I-THEME-1: applying obsidian reproduces the legacy render constants exactly
             (zero visual regression when switching back to obsidian).
  I-THEME-2: _ANSI_MAP is derived (rich Color), covers every themed style,
             and the legacy hand-written table's 4 wrong codes stay fixed.
  I-THEME-3: switch() rejects unknown names; active_name() falls back to
             default on garbage config/env (self-healing).
  I-THEME-4: attic provides every semantic slot obsidian does (theme parity —
             adding a slot to one theme without the other must fail).
  I-THEME-5: switching themes round-trips: attic then obsidian restores
             the legacy palette.
  I-THEME-6: the default theme is attic (希腊美学转正) — regressing the
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


# ── I-THEME-1: obsidian == legacy constants ──


def test_obsidian_reproduces_legacy_palette() -> None:
    theme.apply(theme.OBSIDIAN)
    assert render._C.ACCENT == "gold1"
    assert render._C.SUCCESS == "spring_green3"
    assert render._C.FAIL == "indian_red"
    assert render._C.DANGER == "red3 bold"
    assert render._C.THINKING == "turquoise4"
    assert render._ModeColor.PLAN == "dark_cyan"
    assert render.CODE_THEME == "one-dark"
    assert render.CODE_BG == "#1e1e1e"


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


def test_ansi_derivation_fixes_legacy_wrong_codes() -> None:
    """Counterexample: the old hand-written table had 4 wrong 256-codes.

    rich's own tables are authoritative; regressions to the old values fail.
    """
    legacy_wrong = {
        "spring_green3": "\033[38;5;35m",
        "dark_orange": "\033[38;5;166m",
        "steel_blue1": "\033[38;5;75m",
        "grey37": "\033[38;5;240m",
    }
    m = theme.build_ansi_map(theme.OBSIDIAN)
    for style, wrong in legacy_wrong.items():
        assert m[style] != wrong, f"'{style}' regressed to the wrong legacy code"


def test_ansi_code_handles_bold_and_garbage() -> None:
    assert theme.ansi_code("red3 bold").startswith("\033[1;")
    assert theme.ansi_code("") == ""
    assert theme.ansi_code("not-a-color-xyz") == ""  # counterexample: no crash


# ── I-THEME-3: unknown names / fallback ──


def test_switch_rejects_unknown_theme() -> None:
    with pytest.raises(ValueError, match="unknown theme"):
        theme.switch("corinthian")


def test_active_name_falls_back_on_garbage_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZALL_THEME", "nonexistent-skin")
    assert theme.active_name() == theme.DEFAULT_THEME


def test_active_name_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZALL_THEME", "attic")
    assert theme.active_name() == "attic"


# ── I-THEME-4: theme parity ──


def test_all_themes_fill_every_slot() -> None:
    hex_re = re.compile(r"^#[0-9a-fA-F]{6}$")
    for t in theme.THEMES.values():
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


# ── I-THEME-5: round-trip switch ──


def test_switch_round_trip_restores_default() -> None:
    theme.switch("attic")
    assert render._C.ACCENT == "#c9a227"          # attic laurel gold
    assert render.CODE_THEME == "nord"
    assert "#c9a227" in render._ANSI_MAP           # ANSI 表随主题重建
    theme.switch("obsidian")
    assert render._C.ACCENT == "gold1"             # counterexample twin
    assert render.CODE_THEME == "one-dark"
    assert "gold1" in render._ANSI_MAP
    assert "#c9a227" not in render._ANSI_MAP       # 旧主题条目不得残留


def test_tui_theme_builder_follows_active(monkeypatch: pytest.MonkeyPatch) -> None:
    """TUI 色板构建器从生效主题取值 (启动时派生)。"""
    pytest.importorskip("textual")
    from zall.cli.tui.app import _build_zall_theme
    monkeypatch.setenv("ZALL_THEME", "attic")
    t = _build_zall_theme()
    assert t.name == "zall"                        # Textual 名固定, CSS 不断裂
    assert t.primary == theme.ATTIC.tui_primary
    monkeypatch.setenv("ZALL_THEME", "obsidian")
    t2 = _build_zall_theme()
    assert t2.primary == theme.OBSIDIAN.tui_primary


# ── I-THEME-6: 希腊美学为默认 ──


def test_default_theme_is_attic(monkeypatch: pytest.MonkeyPatch) -> None:
    """默认主题必须是 attic (希腊美学); 反例: 无 env/config 时解析到其它主题即失败。"""
    assert theme.DEFAULT_THEME == "attic"
    monkeypatch.delenv("ZALL_THEME", raising=False)
    monkeypatch.setattr(theme, "_config_theme_name", lambda: "")
    assert theme.active_name() == "attic"
    assert theme.active() is theme.ATTIC
    # 反例孪生: 若有人把默认改回 obsidian, 本断言必然失败
    assert theme.active_name() != "obsidian"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
