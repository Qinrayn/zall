"""choice_menu ↑↓ 菜单 (v0.7 Kimi 交互) 的过滤/自由文本/降级路径测试。

覆盖:
  1. _filter_visible 纯函数: 空文本 → 全量; 大小写不敏感子串匹配 value/label/desc
  2. _menu_window 滑窗: 小列表全量, 超长后当前项始终可见
  3. _render_menu_text: 当前项箭头 / filter 行 / "N more" 行
  4. choice_menu 单行降级 (is_tty=False + input_fn 注入):
     数字直选 / 空输入默认 / 唯一过滤匹配 / 多匹配取首个+提示 /
     无匹配 free_text 回传原文 / 无匹配无 free_text → None / EOF 取消 / 非交互取消
  5. choice_menu 在 out.isatty()=True 时走 ptk; ptk 渲染失败 (抛异常)
     → 降级单行, 同一契约不丢选择 (与真实无终端环境同一条路径)

契约 (与 /provider、网关向导共用): 返回选中 value / 自由文本 / None (取消)。
"""

from __future__ import annotations

import io
from typing import Any

import pytest

import zall.cli.select as select_mod
from zall.cli.select import (
    _filter_visible,
    _menu_window,
    _render_menu_text,
    choice_menu,
)


class _FakeTTY(io.StringIO):
    """isatty()=True 的 StringIO — 模拟终端 (菜单交互分支)。"""

    def isatty(self) -> bool:  # type: ignore[override]
        return True


# ──────────────────────────────────────────────────────────────────────────
# _filter_visible — 纯函数
# ──────────────────────────────────────────────────────────────────────────


class TestFilterVisible:
    _C = [
        ("a", "agnes-2.0-flash", "fast / cheap"),
        ("b", "gpt-4o", "OpenAI, cheap"),
        ("c", "deepseek-chat", "DeepSeek"),
    ]

    def test_empty_text_returns_all(self) -> None:
        """Happy path: 空文本 → 原列表 (不过滤, 顺序不变)。"""
        assert _filter_visible(self._C, "") == list(self._C)
        assert _filter_visible(self._C, "   ") == list(self._C)

    def test_label_substring_case_insensitive(self) -> None:
        """Happy path: label 子串匹配, 大小写不敏感。"""
        assert _filter_visible(self._C, "DEEPSEEK") == [self._C[2]]

    def test_value_substring(self) -> None:
        """Happy path: value (模型 id) 子串匹配。"""
        assert _filter_visible(self._C, "agnes") == [self._C[0]]

    def test_desc_substring(self) -> None:
        """Happy path: desc (说明) 子串匹配。"""
        assert _filter_visible(self._C, "cheap") == [self._C[0], self._C[1]]

    def test_no_match_returns_empty(self) -> None:
        """Counterexample: 无匹配 → 空列表 (不是全量/抛错)。"""
        assert _filter_visible(self._C, "zzzz") == []


# ──────────────────────────────────────────────────────────────────────────
# _menu_window — 超长菜单滑窗
# ──────────────────────────────────────────────────────────────────────────


class TestMenuWindow:
    def test_short_list_full_window(self) -> None:
        """Happy path: 列表不超高 → 全量窗口。"""
        assert _menu_window(0, 5, max_h=10) == (0, 5)

    def test_long_list_keeps_cursor_visible(self) -> None:
        """Happy path: 超长 → 当前项在窗口内, 窗口保持 max_h。"""
        start, end = _menu_window(15, 40, max_h=18)
        assert start <= 15 < end
        assert end - start == 18

    def test_top_anchored(self) -> None:
        """Happy path: 光标近顶 → 窗口从 0 开始。"""
        assert _menu_window(1, 40, max_h=18) == (0, 18)

    def test_bottom_anchored(self) -> None:
        """Happy path: 光标近底 → 窗口贴底不越界。"""
        start, end = _menu_window(39, 40, max_h=18)
        assert end == 40


# ──────────────────────────────────────────────────────────────────────────
# _render_menu_text — 渲染 (含 filter / window)
# ──────────────────────────────────────────────────────────────────────────


class TestRenderMenuText:
    def test_current_arrow(self) -> None:
        """Happy path: 当前项以 ▸ 标记。"""
        lines = _render_menu_text("t", [("a", "x", ""), ("b", "y", "")], current=1)
        assert "▸ y" in lines[2]

    def test_filter_line(self) -> None:
        """Happy path: filter 非空 → 底部出现 [filter: ...] 行。"""
        lines = _render_menu_text(
            "t", [("a", "x", "")], current=0, filter_text="h")
        assert "[filter: h]" in lines[-1]

    def test_window_more_line(self) -> None:
        """Happy path: 窗口不全 → 出现 `… N more` 提示行。"""
        lines = _render_menu_text(
            "t", [("a", "x", ""), ("b", "y", "")], current=0, window=(0, 1))
        assert any("more" in ln for ln in lines)


# ──────────────────────────────────────────────────────────────────────────
# choice_menu — 单行降级 (is_tty=False, input_fn 注入)
# ──────────────────────────────────────────────────────────────────────────


class TestChoiceMenuFallback:
    _C = [
        ("a", "agnes-4", "fast"),
        ("b", "gpt-4o", "OpenAI"),
        ("c", "deepseek-chat", "DeepSeek"),
    ]

    def test_number_selects(self) -> None:
        """数字直选 → 对应 value。"""
        assert choice_menu(io.StringIO(), "t", self._C,
                           is_tty=False, input_fn=lambda _: "2") == "b"

    def test_empty_input_defaults_to_current(self) -> None:
        """空输入 → default_index 项 (安全默认)。"""
        assert choice_menu(io.StringIO(), "t", self._C,
                           is_tty=False, input_fn=lambda _: "", default_index=1) == "b"

    def test_unique_filter_match(self) -> None:
        """文本过滤: 唯一匹配 → 选中; 大小写不敏感。"""
        assert choice_menu(io.StringIO(), "t", self._C,
                           is_tty=False, input_fn=lambda _: "DeepSeek") == "c"

    def test_multiple_matches_pick_first_with_hint(self) -> None:
        """多匹配 → 取首个, 并打印提示 (不吞掉选择)。"""
        out = io.StringIO()
        # "e" 命中 agnes-4 (label) 与 deepseek-chat (value) → 多匹配
        val = choice_menu(out, "t", self._C,
                          is_tty=False, input_fn=lambda _: "e")
        assert val == "a"
        assert "multiple matches" in out.getvalue()

    def test_no_match_free_text_returns_text(self) -> None:
        """无匹配 + free_text=True → 原文返回 (调用方决定直接输入名语义)。"""
        assert choice_menu(io.StringIO(), "t", self._C,
                           is_tty=False, input_fn=lambda _: "my-own-model",
                           free_text=True) == "my-own-model"

    def test_no_match_without_free_text_returns_none(self) -> None:
        """无匹配 + free_text=False → None (取消), 不误选。"""
        assert choice_menu(io.StringIO(), "t", self._C,
                           is_tty=False, input_fn=lambda _: "zzz") is None

    def test_eof_cancels(self) -> None:
        """EOF/KeyboardInterrupt → None (取消, 不给默认)。"""
        def _raise(_p: str) -> str:
            raise EOFError
        assert choice_menu(io.StringIO(), "t", self._C,
                           is_tty=False, input_fn=_raise) is None

    def test_no_input_fn_non_tty_cancels(self) -> None:
        """无输入源 → 'cancelled' + None。"""
        out = io.StringIO()
        assert choice_menu(out, "t", self._C, is_tty=False) is None
        assert "cancelled" in out.getvalue()

    def test_empty_choices_none(self) -> None:
        """Counterexample: 空选项 → None (不崩, 无默认可退)。"""
        assert choice_menu(io.StringIO(), "t", [], is_tty=False,
                           input_fn=lambda _: "1") is None


# ──────────────────────────────────────────────────────────────────────────
# choice_menu — ptk 失败 → 降级 (真终端同一条代码路径)
# ──────────────────────────────────────────────────────────────────────────


class TestChoiceMenuPtkFallback:
    def test_ptk_broken_falls_back_to_input_fn(
            self, monkeypatch: pytest.MonkeyPatch) -> None:
        """ptk 渲染失败 → 单行降级, 同一契约, 选择不丢。"""
        def _boom(*_a: Any, **_k: Any) -> Any:
            raise RuntimeError("no tty")
        monkeypatch.setattr(select_mod, "_ptk_choice_menu", _boom)
        val = choice_menu(_FakeTTY(), "t", TestChoiceMenuFallback._C,
                          input_fn=lambda _: "3")
        assert val == "c"

    def test_ptk_ok_but_cancel_returns_none(
            self, monkeypatch: pytest.MonkeyPatch) -> None:
        """ptk 返回 None (用户取消) → choice_menu 原样返回 None。"""
        monkeypatch.setattr(select_mod, "_ptk_choice_menu",
                            lambda *a, **k: (False, None))
        assert choice_menu(_FakeTTY(), "t", TestChoiceMenuFallback._C) is None

    def test_ptk_ok_selection_echoes_label(
            self, monkeypatch: pytest.MonkeyPatch) -> None:
        """ptk 选中 → 补印一行 (▸ label) 后返回 value。"""
        monkeypatch.setattr(select_mod, "_ptk_choice_menu",
                            lambda *a, **k: (False, "gpt-4o"))
        out = _FakeTTY()
        assert choice_menu(out, "t", TestChoiceMenuFallback._C) == "gpt-4o"
        assert "▸ gpt-4o" in out.getvalue()

    def test_ptk_free_text_passthrough(self,
                                        monkeypatch: pytest.MonkeyPatch) -> None:
        """自由文本标记 → 原文返回 (仅 free_text=True)。"""
        monkeypatch.setattr(select_mod, "_ptk_choice_menu",
                            lambda *a, **k: (True, "custom-name"))
        assert choice_menu(_FakeTTY(), "t", TestChoiceMenuFallback._C,
                           free_text=True) == "custom-name"


def _fake_ptk_pick(out: Any, title: str, choices: Any, **kw: Any) -> Any:
    """给 ptk 打桩的替代实现: 直接按 default_index 取第一项 (无交互)。"""
    idx = kw.get("default_index", 0)
    choices = list(choices)
    return (False, choices[idx][0] if choices else None)