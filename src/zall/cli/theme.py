"""Theme system — single source of truth for all zall colors (G6).

Design (kimi theme.py 对标, 但保留 zall 语义槽位):
  - `Theme` frozen dataclass: 语义槽位 (REPL rich 色 + mode 色 + code 主题 + TUI hex)
  - 渲染代码只引用语义名 (`_C.ACCENT` / Textual 变量), 不写字面色值
  - `apply()` 把主题写入 render 模块 (_C/_ModeColor/_ANSI_MAP/CODE_THEME),
    _ANSI_MAP 由 rich Color 解析自动派生 — 消灭手工 ANSI 对照表 (原三处色源之一)
  - 内置主题 (真实使用反馈后精简为单主题):
      attic — 希腊美学 (唯一): 月桂金 / 爱琴海蓝 / 大理石白 / 陶土红 / 橄榄绿

IPR constraints:
  IPR-0: tests/test_theme_invariants.py (含反例)
  IPR-3: stdlib + rich only
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# ──────────────────────────────────────────────────────────────────────────
# Theme model
# ──────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Theme:
    """语义槽位 → 色值。REPL 槽位接受 rich 色名或 hex; TUI 槽位必须是 hex。"""

    name: str

    # ── REPL 语义槽位 (映射 render._C, 大写同名) ──
    accent: str
    accent2: str
    success: str
    fail: str
    warn: str
    danger: str
    info: str
    dim: str
    subtle: str
    model: str
    thinking: str
    status_bar: str
    status_bar_text: str
    tool_read: str
    tool_write: str
    tool_bash: str
    tool_code: str
    queue: str
    steer: str
    select: str

    # ── 权限模式色 (映射 render._ModeColor) ──
    mode_normal: str
    mode_plan: str
    mode_accept: str
    mode_strict: str

    # ── 代码块 ──
    code_theme: str
    code_bg: str

    # ── TUI 色板 (Textual Theme, hex) ──
    tui_primary: str
    tui_accent: str
    tui_secondary: str
    tui_background: str
    tui_surface: str
    tui_panel: str
    tui_foreground: str
    tui_success: str
    tui_warning: str
    tui_error: str

    # ── diff 渲染色 (G1 cli/diff_render.py; kimi DiffColors 对标) ──
    # bg = 整行背景; hl = 词级内联高亮 (比 bg 更亮一档)
    diff_add_bg: str = "#12261e"
    diff_del_bg: str = "#2d1214"
    diff_add_hl: str = "#1a4a2e"
    diff_del_hl: str = "#5c1a1d"

    _C_SLOTS = (
        "accent", "accent2", "success", "fail", "warn", "danger", "info",
        "dim", "subtle", "model", "thinking", "status_bar", "status_bar_text",
        "tool_read", "tool_write", "tool_bash", "tool_code",
        "queue", "steer", "select",
    )

    def repl_slots(self) -> dict[str, str]:
        """render._C 槽位名 (大写) → 色值。"""
        return {slot.upper(): getattr(self, slot) for slot in self._C_SLOTS}

    def all_styles(self) -> list[str]:
        """全部 REPL/mode 色值 (供 ANSI 表构建与校验)。"""
        vals = [getattr(self, s) for s in self._C_SLOTS]
        vals += [self.mode_normal, self.mode_plan, self.mode_accept, self.mode_strict]
        return [v for v in vals if v]


# ──────────────────────────────────────────────────────────────────────────
# Built-in themes
# ──────────────────────────────────────────────────────────────────────────

# attic — 希腊美学 (Ἀττική): 克制、比例、和谐。
# 月桂金 (胜利花冠) 为主强调; 爱琴海蓝为信息与冷静; 大理石白为正文;
# 陶土红 (黑绘陶器) 为失败; 橄榄绿为成功。低饱和, 如帕特农石面的柔光。
ATTIC = Theme(
    name="attic",
    accent="#d4af37", accent2="#b3953a",
    success="#8bb37f", fail="#d47868",
    warn="#dca54e", danger="#e06c5c bold",
    info="#5aa3c8", dim="#9c9c95", subtle="#74746d",
    model="", thinking="#7f9fb5",
    status_bar="#9c9c95", status_bar_text="#e6e2d6",
    tool_read="#5aa3c8", tool_write="#d4af37",
    tool_bash="#dca54e", tool_code="#8bb37f",
    queue="#5aa3c8", steer="#7f9fb5", select="#5aa3c8",
    mode_normal="#d4af37", mode_plan="#5aa3c8",
    mode_accept="#8bb37f", mode_strict="#d47868",
    code_theme="nord", code_bg="#16181d",
    tui_primary="#d4af37", tui_accent="#d4af37", tui_secondary="#5aa3c8",
    tui_background="#15171a", tui_surface="#1c1f23", tui_panel="#262a30",
    tui_foreground="#e6e2d6",
    tui_success="#8bb37f", tui_warning="#dca54e", tui_error="#d47868",
    # 橄榄绿/陶土红调的 diff 背景 (与 attic 色板同座标系)
    diff_add_bg="#1a2418", diff_del_bg="#2a1713",
    diff_add_hl="#2e4527", diff_del_hl="#552b22",
)

THEMES: dict[str, Theme] = {ATTIC.name: ATTIC}
# 单主题 (真实使用反馈: 多主题没啥用, 只保留希腊美学 attic)。
DEFAULT_THEME = "attic"


# ──────────────────────────────────────────────────────────────────────────
# Active theme resolution
# ──────────────────────────────────────────────────────────────────────────


def active_name() -> str:
    """解析生效主题名: env ZALL_THEME > config [ui].theme > default。

    未知名回退 default (配置自愈, 不崩)。
    """
    import os
    name = (os.environ.get("ZALL_THEME") or "").strip().lower()
    if not name:
        name = _config_theme_name()
    return name if name in THEMES else DEFAULT_THEME


def _config_theme_name() -> str:
    try:
        from zall._util.toml import load_toml_simple
        path = Path.home() / ".zall" / "config.toml"
        if not path.exists():
            return ""
        data = load_toml_simple(path)
        ui = data.get("ui", {})
        if isinstance(ui, dict):
            return str(ui.get("theme", "")).strip().lower()
    except Exception:
        pass
    return ""


def active() -> Theme:
    return THEMES[active_name()]


# 最后一次 apply() 的主题 (运行时真相; active() 只反映 env/config 解析)。
# 区分两者: 测试/热切换可能直接 apply(THEME) 而不改 env。
_current: Theme | None = None


def current() -> Theme:
    """当前已应用的主题 (渲染代码应用此而非 active())。"""
    return _current if _current is not None else active()


# ──────────────────────────────────────────────────────────────────────────
# ANSI derivation (raw-stream writes: spinner/progress 无法走 rich markup)
# ──────────────────────────────────────────────────────────────────────────


def ansi_code(style: str) -> str:
    """rich 色名/hex → ANSI escape 前缀。自动派生, 无手工对照表。

    支持尾缀修饰 (如 "red3 bold" → bold + 色码); 解析失败返回 ""
    (调用方 _ansi() 语义: 空码 = 不着色, 优雅降级)。
    """
    if not style:
        return ""
    parts = style.split()
    bold = "bold" in parts
    color_token = next((p for p in parts if p != "bold"), "")
    if not color_token:
        return "\033[1m" if bold else ""
    try:
        from rich.color import Color
        codes = Color.parse(color_token).get_ansi_codes(foreground=True)
    except Exception:
        return ""
    prefix = "1;" if bold else ""
    return f"\033[{prefix}{';'.join(codes)}m"


def build_ansi_map(theme: Theme) -> dict[str, str]:
    """主题全部色值 → {色值: ANSI 前缀} (render._ANSI_MAP 替换体)。"""
    return {style: ansi_code(style) for style in theme.all_styles() if ansi_code(style)}


# ──────────────────────────────────────────────────────────────────────────
# Application (写入 render 模块 — 唯一允许触碰 _C 的地方)
# ──────────────────────────────────────────────────────────────────────────


def apply(theme: Theme) -> None:
    """把主题写入 render 模块的 _C/_ModeColor/_ANSI_MAP/CODE_*。

    lazy import 避免环 (render 模块尾部 import theme 并调 apply(active()))。
    """
    global _current
    _current = theme
    from zall.cli import render
    for slot, value in theme.repl_slots().items():
        setattr(render._C, slot, value)
    render._ModeColor.NORMAL = theme.mode_normal
    render._ModeColor.PLAN = theme.mode_plan
    render._ModeColor.ACCEPT = theme.mode_accept
    render._ModeColor.STRICT = theme.mode_strict
    render._ANSI_MAP.clear()
    render._ANSI_MAP.update(build_ansi_map(theme))
    render.CODE_THEME = theme.code_theme
    render.CODE_BG = theme.code_bg
    # 共享 console 缓存持有旧主题的 rich Theme (语义样式名字典) — 清掉重建
    render.clear_console_cache()


def switch(name: str) -> Theme:
    """按名切换并应用; 未知名抛 ValueError (调用方给用户可读错误)。"""
    key = (name or "").strip().lower()
    if key not in THEMES:
        raise ValueError(
            f"unknown theme '{name}' (available: {', '.join(sorted(THEMES))})"
        )
    theme = THEMES[key]
    apply(theme)
    return theme


def list_themes() -> list[str]:
    return sorted(THEMES)


__all__ = [
    "ATTIC",
    "DEFAULT_THEME",
    "THEMES",
    "Theme",
    "active",
    "active_name",
    "ansi_code",
    "apply",
    "build_ansi_map",
    "current",
    "list_themes",
    "switch",
]
