"""Selector widget + REPL 数字选择器 test (Part B/E).

covers:
  1. parse_selection 纯逻辑: 空/非法/越界 → 安全默认 (default_index)
  2. render_choices 纯文本渲染 (无 emoji, 标默认)
  3. select_prompt REPL 数字选择器 (注入 input_fn): happy + EOF/越界回退
  4. SelectMenu widget (未挂载可测): open/close/move(wrap)/select_number/selected_value

IPR-0: 每个 test 含 counterexample。
"""

from __future__ import annotations

import io

import pytest
pytest.importorskip("textual")

from zall.cli.select import parse_selection, render_choices, select_prompt
from zall.cli.tui.widgets import SelectMenu

_CHOICES = [
    ("y", "allow once", "run this tool call"),
    ("n", "reject", "skip this tool call"),
    ("a", "always allow", "auto-allow this session"),
]


# ──────────────────────────────────────────────────────────────────────────
# parse_selection — 纯逻辑, 安全默认
# ──────────────────────────────────────────────────────────────────────────


class TestParseSelection:
    def test_valid_number_maps_to_zero_based_index(self) -> None:
        """Happy path: '2' (1-based) → 下标 1。"""
        assert parse_selection("2", 3) == 1

    def test_first_and_last_boundaries(self) -> None:
        """Happy path: 边界 '1' → 0, 'n' → n-1。"""
        assert parse_selection("1", 3) == 0
        assert parse_selection("3", 3) == 2

    def test_empty_returns_default(self) -> None:
        """Counterexample: 空输入 → default_index (不是 0/报错)。"""
        assert parse_selection("", 3, default_index=1) == 1

    def test_out_of_range_returns_default(self) -> None:
        """Counterexample: 越界 (0 或 >n) → default, 绝不返回越界下标。"""
        assert parse_selection("0", 3, default_index=2) == 2
        assert parse_selection("9", 3, default_index=2) == 2

    def test_non_digit_returns_default(self) -> None:
        """Counterexample: 非数字 (含负号/小数/字母) → default。"""
        assert parse_selection("abc", 3, default_index=1) == 1
        assert parse_selection("-1", 3, default_index=1) == 1
        assert parse_selection("1.5", 3, default_index=1) == 1

    def test_whitespace_is_stripped(self) -> None:
        """Happy path: 前后空白被裁剪, '  2  ' → 下标 1。"""
        assert parse_selection("  2  ", 3) == 1

    def test_empty_choices_returns_minus_one(self) -> None:
        """Counterexample: n<=0 → -1 (无可选项)。"""
        assert parse_selection("1", 0) == -1

    def test_default_index_out_of_range_falls_back_to_zero(self) -> None:
        """Counterexample: 传入越界 default_index → 回退 0 (不崩)。"""
        assert parse_selection("", 3, default_index=99) == 0


# ──────────────────────────────────────────────────────────────────────────
# render_choices — 纯文本, 无 emoji
# ──────────────────────────────────────────────────────────────────────────


class TestRenderChoices:
    def test_contains_title_and_labels(self) -> None:
        """Happy path: 标题 + 每个选项 label/desc 都出现, 带 1-based 编号。"""
        out = render_choices("pick one", _CHOICES)
        assert "pick one" in out
        assert "1. allow once" in out
        assert "2. reject" in out
        assert "3. always allow" in out
        assert "run this tool call" in out

    def test_default_marked_with_asterisk(self) -> None:
        """Happy path: default_index 项用 '*' 标记, 其余用空格。"""
        out = render_choices("t", _CHOICES, default_index=1)
        lines = out.splitlines()
        # 找到含 'reject' 的行 (default), 应含 '*'
        reject_line = next(ln for ln in lines if "reject" in ln)
        assert "*2." in reject_line

    def test_no_emoji(self) -> None:
        """Counterexample: 输出不含 emoji (纯 ASCII 编号菜单)。"""
        out = render_choices("t", _CHOICES)
        # 常见 emoji / 装饰符不应出现
        for ch in ("\u2714", "\u2705", "\U0001f7e2", "\u26a0\ufe0f"):
            assert ch not in out


# ──────────────────────────────────────────────────────────────────────────
# select_prompt — REPL 数字选择器 (注入 input_fn)
# ──────────────────────────────────────────────────────────────────────────


class TestSelectPrompt:
    def test_valid_selection_returns_value(self) -> None:
        """Happy path: 输 '3' → 返回第 3 项 value 'a'。"""
        buf = io.StringIO()
        val = select_prompt(buf, "pick", _CHOICES, input_fn=lambda _: "3")
        assert val == "a"

    def test_empty_input_returns_default_value(self) -> None:
        """Happy path: 空输入 (Enter) → default_index 项 value。"""
        buf = io.StringIO()
        val = select_prompt(buf, "pick", _CHOICES, input_fn=lambda _: "", default_index=1)
        assert val == "n"

    def test_out_of_range_falls_back_to_default(self) -> None:
        """Counterexample: 越界输入 → default value (不返回越界项/不崩)。"""
        buf = io.StringIO()
        val = select_prompt(buf, "pick", _CHOICES, input_fn=lambda _: "99", default_index=0)
        assert val == "y"

    def test_eof_falls_back_to_default(self) -> None:
        """Counterexample: input_fn 抛 EOFError (非交互) → default value, 不崩。"""
        def _raise(_: str) -> str:
            raise EOFError

        buf = io.StringIO()
        val = select_prompt(buf, "pick", _CHOICES, input_fn=_raise, default_index=1)
        assert val == "n"

    def test_keyboard_interrupt_falls_back_to_default(self) -> None:
        """Counterexample: Ctrl+C (KeyboardInterrupt) → default value, 不崩。"""
        def _raise(_: str) -> str:
            raise KeyboardInterrupt

        buf = io.StringIO()
        val = select_prompt(buf, "pick", _CHOICES, input_fn=_raise, default_index=0)
        assert val == "y"

    def test_empty_choices_returns_empty_string(self) -> None:
        """Counterexample: 无选项 → 空串 (不读输入/不崩)。"""
        buf = io.StringIO()
        called = {"n": 0}

        def _count(_: str) -> str:
            called["n"] += 1
            return "1"

        val = select_prompt(buf, "pick", [], input_fn=_count)
        assert val == ""
        assert called["n"] == 0  # 不读输入

    def test_prompt_text_written_to_out(self) -> None:
        """Happy path: 选项被写入 out (用户可见编号菜单)。"""
        buf = io.StringIO()
        select_prompt(buf, "choose action", _CHOICES, input_fn=lambda _: "1")
        written = buf.getvalue()
        assert "choose action" in written
        assert "allow once" in written


# ──────────────────────────────────────────────────────────────────────────
# SelectMenu widget — 未挂载可测 (导航/数字/选中/取消逻辑)
# ──────────────────────────────────────────────────────────────────────────


class TestSelectMenuWidget:
    def _open(self) -> SelectMenu:
        m = SelectMenu()
        m.open("confirm tool call", list(_CHOICES))
        return m

    def test_open_sets_is_open_and_first_selected(self) -> None:
        """Happy path: open → is_open True, 默认选中第一项。"""
        m = self._open()
        assert m.is_open is True
        assert m.selected_value == "y"

    def test_closed_before_open(self) -> None:
        """Counterexample: 未 open → is_open False, selected_value None。"""
        m = SelectMenu()
        assert m.is_open is False
        assert m.selected_value is None

    def test_move_down_and_up(self) -> None:
        """Happy path: move(1) 下移, move(-1) 上移。"""
        m = self._open()
        m.move(1)
        assert m.selected_value == "n"
        m.move(-1)
        assert m.selected_value == "y"

    def test_move_wraps_around(self) -> None:
        """Counterexample: 从第一项 move(-1) → 环绕到最后一项 (不越界/不崩)。"""
        m = self._open()
        m.move(-1)
        assert m.selected_value == "a"  # 环绕到末项
        m.move(1)
        assert m.selected_value == "y"  # 从末项环回首项

    def test_select_number_in_range(self) -> None:
        """Happy path: select_number(2) (1-based) → 选中第 2 项, 返回 True。"""
        m = self._open()
        assert m.select_number(2) is True
        assert m.selected_value == "n"

    def test_select_number_out_of_range_is_noop(self) -> None:
        """Counterexample: 越界数字 → 返回 False, 选中项不变。"""
        m = self._open()
        assert m.select_number(9) is False
        assert m.selected_value == "y"  # 不变
        assert m.select_number(0) is False
        assert m.selected_value == "y"

    def test_close_clears_open_state(self) -> None:
        """Happy path (Esc 语义): close → is_open False, selected_value None。"""
        m = self._open()
        m.close()
        assert m.is_open is False
        assert m.selected_value is None

    def test_caps_at_nine_choices(self) -> None:
        """Counterexample: >9 选项 → 只保留前 9 (数字键 1-9 可覆盖)。"""
        many = [(str(i), f"opt{i}", "") for i in range(12)]
        m = SelectMenu()
        m.open("many", many)
        # 第 10 项 (下标 9) 不应可选中
        assert m.select_number(9) is True   # 第 9 项存在
        assert m.select_number(10) is False  # 第 10 项被截断

    def test_render_contains_labels_and_hint(self) -> None:
        """Happy path: render 输出含选项 label + 底部键位提示 (无 emoji)。"""
        m = self._open()
        plain = m.render().plain
        assert "allow once" in plain
        assert "reject" in plain
        # 键位提示行 (↑↓ / 1-9 / Enter / Esc)
        assert "Enter" in plain
        assert "Esc" in plain

    def test_message_classes_exist(self) -> None:
        """结构契约: ChatTextArea 选择/中断消息 + InputBar 回传消息齐全。

        Counterexample: 缺任一消息类 → 键盘→选择/中断链断裂。
        """
        from zall.cli.tui.widgets import ChatTextArea, InputBar
        for name in ("SelectPrev", "SelectNext", "SelectNum", "SelectConfirm",
                     "SelectCancel", "Interrupt"):
            assert hasattr(ChatTextArea, name), f"ChatTextArea.{name} missing"
        for name in ("SelectChosen", "SelectCancelled", "Interrupt"):
            assert hasattr(InputBar, name), f"InputBar.{name} missing"
