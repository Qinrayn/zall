"""命令面板 (fuzzy_rank / get_palette_commands) + 工具输出去 emoji 不变量。

对应 TUI 交互重塑:
  - 模糊命令面板让"命令多"不再是问题 (前缀>子串>子序列, core 优先, 无别名噪音)。
  - 深度重构渲染要求工具输出不含 emoji。

不变量 (each with counterexample):
  A  fuzzy_rank: 前缀 > 子串 > 子序列 (排序档位)。
  B  同档 core 优先 (反例: 非 core 不得排到 core 前)。
  C  desc 命中是降级项 (name 未中仍可经描述召回)。
  D  完全不匹配 → 剔除 (反例: 噪音不进面板)。
  E  空 query → 原序前 limit。
  F  get_palette_commands 只含规范名 (反例: 别名不出现); /lab 属 core。
  G  工具模块 (project_analysis / code_understanding) 源码不含 emoji。
"""

from __future__ import annotations

import inspect
import re

import pytest

from zall.cli.commands import fuzzy_rank, get_palette_commands


# ══════════════════════════════════════════════════════════════════
# fuzzy_rank
# ══════════════════════════════════════════════════════════════════
class TestFuzzyRank:
    def test_prefix_beats_substring_beats_subsequence(self) -> None:
        items = [
            ("/aaa", "", "", False),   # 前缀命中 (score 0)
            ("/baa", "", "", False),   # 子串命中 (score 1)
            ("/axa", "", "", False),   # 子序列命中 (score 2)
        ]
        names = [n for n, _, _, _ in fuzzy_rank("aa", items)]
        assert names == ["/aaa", "/baa", "/axa"]              # A

    def test_core_priority_on_tie(self) -> None:
        items = [
            ("/codegraph", "code symbol search", "Code", False),
            ("/compact", "compress context", "Session", True),
        ]
        names = [n for n, _, _, _ in fuzzy_rank("co", items)]
        # 同为前缀档 → core 的 /compact 必须排在非 core 的 /codegraph 前 (B 反例)
        assert names.index("/compact") < names.index("/codegraph")

    def test_desc_match_is_fallback(self) -> None:
        items = [
            ("/compact", "compress conversation context", "Session", True),
            ("/model", "show or switch model", "Model", True),
        ]
        names = [n for n, _, _, _ in fuzzy_rank("context", items)]
        assert "/compact" in names and "/model" not in names   # C

    def test_no_match_excluded(self) -> None:
        items = [("/model", "switch model", "Model", True)]
        assert fuzzy_rank("zzzzz", items) == []                # D 反例

    def test_empty_query_returns_prefix(self) -> None:
        items = [("/a", "", "", False), ("/b", "", "", False), ("/c", "", "", False)]
        assert fuzzy_rank("", items, limit=2) == items[:2]     # E

    def test_slash_prefix_stripped(self) -> None:
        items = [("/model", "switch model", "Model", True)]
        assert [n for n, _, _, _ in fuzzy_rank("/mod", items)] == ["/model"]

    def test_limit_respected(self) -> None:
        items = [(f"/c{i}", "", "", False) for i in range(20)]
        assert len(fuzzy_rank("c", items, limit=5)) == 5


# ══════════════════════════════════════════════════════════════════
# get_palette_commands
# ══════════════════════════════════════════════════════════════════
class TestPaletteCommands:
    def test_excludes_aliases(self) -> None:
        names = {n for n, _, _, _ in get_palette_commands()}
        # 反例: 别名 (/h /q /v /sug /selfplay /improve /rl) 不该进面板
        for alias in ("/h", "/q", "/v", "/sug", "/selfplay", "/improve", "/rl"):
            assert alias not in names, f"alias {alias} leaked into palette"

    def test_canonical_present(self) -> None:
        names = {n for n, _, _, _ in get_palette_commands()}
        for canonical in ("/help", "/model", "/lab", "/sessions", "/doctor"):
            assert canonical in names

    def test_lab_and_model_are_core(self) -> None:
        core = {n for n, _, _, is_core in get_palette_commands() if is_core}
        assert "/lab" in core and "/model" in core and "/mode" in core

    def test_advanced_command_not_core(self) -> None:
        # 反例: 高级/低频命令 (/checkpoint) 不该标记为 core
        non_core = {n for n, _, _, is_core in get_palette_commands() if not is_core}
        assert "/checkpoint" in non_core and "/evolve" in non_core

    def test_deleted_and_demoted_absent_from_palette(self) -> None:
        from zall.cli.commands import get_known_commands
        names = {n for n, _, _, _ in get_palette_commands()}
        reg = get_known_commands()
        # /web /search 已删除: 不在注册表也不在面板 (反例)
        assert "/web" not in reg and "/search" not in reg
        assert "/web" not in names and "/search" not in names
        # /fix /review /git /commit 降级: 不在面板, 但仍可执行 (在注册表)
        for c in ("/fix", "/review", "/git", "/commit"):
            assert c not in names, f"{c} should be demoted from palette"
            assert c in reg, f"{c} must remain runnable"


class TestAliasAnnotation:
    """G17 (kimi "/name (alias)" 对标): 展示层别名标注不变量。

    H  别名条目标 "→ 规范名"; 规范名条目尾附 "(alias: ...)"。
    I  无别名命令 desc 无标注 (反例: 不污染)。
    J  面板 desc 附别名 → fuzzy desc 命中可经别名召回。
    """

    def test_alias_entry_points_to_canonical(self) -> None:
        from zall.cli.commands._common import get_command_meta
        meta = get_command_meta()
        assert meta["/h"].startswith("→ /help")
        assert meta["/q"].startswith("→ /exit")

    def test_canonical_entry_lists_aliases(self) -> None:
        from zall.cli.commands._common import get_command_meta
        meta = get_command_meta()
        assert "alias: /h" in meta["/help"]
        assert "/quit" in meta["/exit"] and "/q" in meta["/exit"]

    def test_no_alias_no_annotation_counterexample(self) -> None:
        """反例: 无别名命令的 desc 不被标注污染。"""
        from zall.cli.commands._common import _COMMANDS, get_command_meta
        meta = get_command_meta()
        bare = [c for c in _COMMANDS.values() if not c.aliases]
        assert bare, "需至少一个无别名命令作反例"
        for cmd in bare:
            assert "(alias:" not in meta[cmd.name]
            assert not meta[cmd.name].startswith("→ ")

    def test_palette_desc_enables_alias_recall(self) -> None:
        """J: 面板搜 "selfplay" 能经 desc 里的别名召回 /lab。"""
        items = get_palette_commands()
        lab_desc = next(d for n, d, _, _ in items if n == "/lab")
        assert "/selfplay" in lab_desc
        ranked = fuzzy_rank("selfplay", items)
        assert any(n == "/lab" for n, _, _, _ in ranked)


class TestPaletteBehavior:
    """InputBar._refresh_menu + CommandMenu 面板行为 (未挂载可测)。"""

    @pytest.fixture(autouse=True)
    def _require_textual(self):
        pytest.importorskip("textual")

    def _bar(self, text: str):
        from zall.cli.tui.widgets import InputBar
        b = InputBar()
        b._textarea.text = text
        b._refresh_menu()
        return b

    def test_empty_query_shows_all_core_first(self) -> None:
        b = self._bar("/")
        shown = [n for n, _ in b._menu._items]
        assert shown                                       # 非空
        all_cmds = get_palette_commands()
        # 修 “显示不完全”: 空 query 现展示全部命令 (可滚动), 而非只 8 条 core
        assert len(shown) == len(all_cmds)
        core = {n[1:] for n, _, _, c in all_cmds if c}
        assert shown[0] in core                            # core 优先: 首项是 core
        assert any(n not in core for n in shown)           # 反例: 非 core 命令也收入 (不丢弃)
        assert b._menu._hint                               # 提示行存在

    def test_typing_filters_no_double_slash(self) -> None:
        b = self._bar("/mod")
        shown = [n for n, _ in b._menu._items]
        # 反例: 面板内部名不得带 / (否则 render/补全会出现 //cmd)
        assert all(not n.startswith("/") for n in shown)
        assert "mode" in shown and "model" in shown
        assert not (b._menu.selected_command or "").startswith("/")

    def test_command_completion_single_slash(self) -> None:
        b = self._bar("/mod")
        b._menu._selected = [n for n, _ in b._menu._items].index("model")
        b._complete_menu_selection()
        assert b._textarea.text == "/model "               # 反例: 不是 //model

    def test_render_shows_hint_and_single_slash(self) -> None:
        b = self._bar("/mod")
        rendered = b._menu.render()
        text = rendered.plain if hasattr(rendered, "plain") else str(rendered)
        assert "Tab" in text and "Esc" in text              # 提示行渲染
        assert "//" not in text                            # 反例: 无双斜杠


# ══════════════════════════════════════════════════════════════════
# 工具输出去 emoji
# ══════════════════════════════════════════════════════════════════
# 仅匹配真正的 emoji 图形区 (不误伤 ✓✗⚠ 等单色几何符号)
_EMOJI = re.compile(r"[\U0001F000-\U0001FAFF]")


class TestNoEmoji:
    def test_tool_modules_have_no_emoji(self) -> None:
        import zall.tools.code_understanding as cu
        import zall.tools.project_analysis as pa
        for mod in (pa, cu):
            found = _EMOJI.findall(inspect.getsource(mod))
            assert not found, f"{mod.__name__} still contains emoji: {found}"
