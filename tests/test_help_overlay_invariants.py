"""`?` 快捷键帮助浮层 (Codex ?overlay / Claude 空输入 ? 同款)。

学 Codex CLI key-hint 思想: 帮助面板显示的是**真实绑定** (BINDINGS + 输入区
固定键路), 不是硬编码假文案 — "帮助说的一套 = 实际可用的一套", 换键自动跟随。

不变量 (含 IPR-0 反例):
  - 空输入 (含纯空白) 按 `?` → toggle 帮助; 非空输入中的 `?` 必须照常输入 (反例)。
  - select_mode (确认门) 中 `?` 不抢键 (反例: 决策键由菜单独自处理)。
  - 帮助打开时 Esc → 只关面板 (HelpDismiss), 绝不触发 Interrupt (反例);
    帮助关闭时 Esc → 照常 Interrupt (反例: 不吞 Esc)。
  - toggle 是对合 (involution): 开→关 回到原态, help_open 标志同步。
  - 帮助行包含 BINDINGS 中每个 show=True 键 + 输入区固定功能键 (防"帮助说一套")。
  - 未挂载时 action_toggle_help 幂等不崩 (反例)。
  - HelpOverlay: CSS 无字面 hex; 渲染字符只在 ASCII 回退后的安全集内 (无 emoji)。
"""

from __future__ import annotations

import asyncio
import re
from unittest import mock

import pytest

pytest.importorskip("textual")


class _KeySim:
    """最小 Key 事件模拟: 只暴露 _on_key 分支用到的接口。"""

    def __init__(self, key: str) -> None:
        self.key = key
        self.stopped = False
        self.prevented = False

    def stop(self) -> None:
        self.stopped = True

    def prevent_default(self) -> None:
        self.prevented = True


def _drive_key(ta, key: str, *, text: str = "", help_open: bool = False) -> tuple[list[str], bool]:
    """对 ChatTextArea 实例模拟一次 _on_key (不挂载)。

    返回 (投递的消息名列表, 是否落到 super 文本输入路径)。super()._on_key 用
    mock 换掉, 只验证我们自己的键路由分支 — 与 Textual 文本输入机制解耦。
    """
    ta.text = text
    ta.help_open = help_open
    posted: list[str] = []
    sim = _KeySim(key)
    hit_super = {"v": False}
    from textual.widgets import TextArea as _BaseTextArea

    async def _fake_super(self, _event: _KeySim) -> None:  # noqa: B027 (class-method 绑定签名带 self)
        hit_super["v"] = True

    async def _drive() -> None:
        with mock.patch.object(_BaseTextArea, "_on_key", new=_fake_super):
            with mock.patch.object(
                ta, "post_message", lambda m: posted.append(m.__class__.__name__)
            ):
                await ta._on_key(sim)

    asyncio.run(_drive())
    assert sim.stopped == bool(posted), "被拦截的键 (有消息) 必须 stop, 反之不得 stop"
    return posted, hit_super["v"]


# ──────────────────────────────────────────────────────────────────────────
# 1. route_help_key — 纯函数路由判定 (IPR-0 表)
# ──────────────────────────────────────────────────────────────────────────


class TestHelpRoute:
    def test_question_mark_on_empty_input_toggles(self) -> None:
        from zall.cli.tui.widgets import route_help_key

        assert route_help_key("question_mark", "", help_open=False) == "toggle"
        assert route_help_key("?", "", help_open=False) == "toggle"
        # 纯空白也算空 ("看起来空"但非空串的输入)
        assert route_help_key("question_mark", "   ", help_open=False) == "toggle"
        # 打开时再按 ? → 同样是 toggle (App 侧取反 = 关闭), 对合可用
        assert route_help_key("?", "", help_open=True) == "toggle"

    def test_typed_question_mark_is_never_captured(self) -> None:
        """反例: 非空输入中的 ? 是字符, 绝不打开帮助 (不抢键)。"""
        from zall.cli.tui.widgets import route_help_key

        assert route_help_key("?", "what time is it", help_open=False) is None
        assert route_help_key("?", "a?", help_open=False) is None
        assert route_help_key("question_mark", "  x", help_open=False) is None

    def test_select_mode_owns_keys(self) -> None:
        """反例: 确认门 (select_mode) 中 ? / Esc 不得抢键 — 决策键由菜单独占。"""
        from zall.cli.tui.widgets import route_help_key

        assert route_help_key("?", "", select_mode=True, help_open=False) is None
        assert route_help_key("?", "", select_mode=True, help_open=True) is None
        assert route_help_key("escape", "", select_mode=True, help_open=True) is None

    def test_escape_semantics_depend_on_help_open(self) -> None:
        """Esc: 帮助打开 → dismiss (只关面板); 帮助关闭 → None (正常落 Interrupt)。"""
        from zall.cli.tui.widgets import route_help_key

        assert route_help_key("escape", "", help_open=True) == "dismiss"
        assert route_help_key("escape", "half typed", help_open=True) == "dismiss"
        assert route_help_key("escape", "", help_open=False) is None

    def test_unrelated_keys_never_route(self) -> None:
        from zall.cli.tui.widgets import route_help_key

        for key in ("enter", "up", "down", "tab", "shift+enter", "ctrl+c", "a", "1"):
            assert route_help_key(key, "", help_open=False) is None
            assert route_help_key(key, "", help_open=True) is None


# ──────────────────────────────────────────────────────────────────────────
# 2. ChatTextArea._on_key 端到端键路 (模拟事件, 不挂载)
# ──────────────────────────────────────────────────────────────────────────


class TestChatTextAreaKeyWiring:
    def test_question_mark_empty_posts_toggle(self) -> None:
        from zall.cli.tui.widgets import ChatTextArea

        posted, hit_super = _drive_key(ChatTextArea(), "?")
        assert posted == ["ToggleHelp"]
        assert hit_super is False

    def test_question_mark_with_text_typing_wins(self) -> None:
        """反例: 非空输入按 ? → 不投帮助消息, 键落文本管线。"""
        from zall.cli.tui.widgets import ChatTextArea

        posted, hit_super = _drive_key(ChatTextArea(), "question_mark", text="hello ?")
        assert posted == []
        assert hit_super is True

    def test_esc_with_help_open_posts_dismiss_not_interrupt(self) -> None:
        """反例 (核心): 帮助打开时 Esc → HelpDismiss, 绝无 Interrupt 投递。"""
        from zall.cli.tui.widgets import ChatTextArea

        posted, _ = _drive_key(ChatTextArea(), "escape", help_open=True)
        assert posted == ["HelpDismiss"]
        assert "Interrupt" not in posted

    def test_esc_with_help_closed_still_interrupts(self) -> None:
        """反例: 帮助关闭时 Esc 照常中断 (不吞 Esc, 兼容既有行为)。"""
        from zall.cli.tui.widgets import ChatTextArea

        posted, _ = _drive_key(ChatTextArea(), "escape", help_open=False)
        assert posted == ["Interrupt"]


# ──────────────────────────────────────────────────────────────────────────
# 3. InputBar 透传与 toggle 对合
# ──────────────────────────────────────────────────────────────────────────


class TestInputBarToggle:
    def test_toggle_help_is_involution_and_syncs_flag(self) -> None:
        from zall.cli.tui.widgets import InputBar

        bar = InputBar()
        assert bar._help.is_open is False
        assert bar._textarea.help_open is False

        opened = bar.toggle_help()
        assert opened is True
        assert bar._help.is_open is True
        assert bar._textarea.help_open is True, "textArea 标志必须同步 (Esc 路由依赖它)"

        closed = bar.toggle_help()
        assert closed is False
        assert bar._help.is_open is False
        assert bar._textarea.help_open is False, "二次 toggle 必须回到原态 (对合)"

    def test_dismiss_help_closes_and_clears_flag(self) -> None:
        from zall.cli.tui.widgets import InputBar

        bar = InputBar()
        bar.toggle_help()
        bar.dismiss_help()
        assert bar._help.is_open is False
        assert bar._textarea.help_open is False

    def test_help_messages_reposted_to_app(self) -> None:
        from zall.cli.tui.widgets import ChatTextArea, InputBar

        bar = InputBar()
        posted: list[str] = []
        bar.post_message = lambda m: posted.append(m.__class__.__name__)  # type: ignore[method-assign,assignment]
        bar.on_chat_text_area_toggle_help(mock.Mock(spec=ChatTextArea.ToggleHelp))
        bar.on_chat_text_area_help_dismiss(mock.Mock(spec=ChatTextArea.HelpDismiss))
        assert posted == ["ToggleHelp", "HelpDismiss"]

    def test_message_pipeline_matches_chat_text_area(self) -> None:
        """透传契约: InputBar 的消息与 ChatTextArea 发送的一一对应。"""
        from zall.cli.tui.widgets import ChatTextArea, InputBar

        assert InputBar.ToggleHelp is not ChatTextArea.ToggleHelp
        assert InputBar.ToggleHelp.__name__ == "ToggleHelp"
        assert InputBar.HelpDismiss.__name__ == "HelpDismiss"


# ──────────────────────────────────────────────────────────────────────────
# 4. 帮助数据 = 真实绑定 (防"帮助说一套")
# ──────────────────────────────────────────────────────────────────────────


class TestHelpRowsMatchBindings:
    def test_rows_cover_every_shown_binding(self) -> None:
        from zall.cli.tui import TuiApp

        app = TuiApp()
        rows = app._help_rows()
        keys = [k for k, _ in rows]
        shown = [b.key for b in app.BINDINGS if b.show and b.key != "question_mark"]
        for binding_key in shown:
            assert binding_key in keys, f"绑定 {binding_key} 必须出现在帮助里 (Codex key-hint)"
        # 打开键自身由面板底部提示行 (esc close · ? toggle) 说明, 不重复列一行
        assert "question_mark" not in keys

    def test_binding_rows_are_the_binding_payload(self) -> None:
        """帮助行里的绑定区段与 BINDINGS 逐条一致 (没有编造/过期的键)。"""
        from zall.cli.tui import TuiApp

        app = TuiApp()
        rows = app._help_rows()
        shown = [(b.key, b.description) for b in app.BINDINGS if b.show and b.key != "question_mark"]
        assert rows[: len(shown)] == shown

    def test_input_function_rows_present(self) -> None:
        from zall.cli.tui import TuiApp

        keys = [k for k, _ in TuiApp()._help_rows()]
        for key in ("enter", "shift+enter", "up/down", "/", "@", "tab", "esc"):
            assert key in keys, f"输入区固定功能 {key} 必须可帮助 (实际键路在 ChatTextArea)"

    def test_existing_critical_bindings_untouched(self) -> None:
        """兼容不变量: 既有快捷键必须仍在 (新增 ? 不得挤掉任何键)。"""
        from zall.cli.tui import TuiApp

        keys = {b.key for b in TuiApp().BINDINGS}
        for existing in ("shift+tab", "ctrl+o", "ctrl+s", "ctrl+c", "ctrl+l", "ctrl+q"):
            assert existing in keys


# ──────────────────────────────────────────────────────────────────────────
# 5. 健壮性 & 渲染约束
# ──────────────────────────────────────────────────────────────────────────


class TestOverlayRobustness:
    def test_action_toggle_help_unmounted_noop(self) -> None:
        """反例: 未挂载 (无 #input-bar) 时调用必须静默不崩。"""
        from zall.cli.tui import TuiApp

        app = TuiApp()
        app.action_toggle_help()
        app.action_toggle_help()

    def test_overlay_hidden_by_default_and_show_hide_switch(self) -> None:
        from zall.cli.tui.widgets import HelpOverlay

        h = HelpOverlay()
        assert h.is_open is False, "帮助面板默认必须隐藏"
        h.show()
        assert h.is_open is True
        h.hide()
        assert h.is_open is False

    def test_overlay_css_has_no_literal_hex(self) -> None:
        """主题约束: CSS 只用 $变量, 任何字面 #hex 都是违规 (与 test_theme 同规则)。"""
        from zall.cli.tui.widgets import HelpOverlay

        assert "#" not in HelpOverlay.DEFAULT_CSS
        # 也只允许主题变量 ($surface/$panel), 不允许裸颜色名兜底
        for token in ("red", "green", "blue", "yellow"):
            assert f":{token}" not in HelpOverlay.DEFAULT_CSS

    def test_overlay_render_emoji_free_under_ascii_fallback(self) -> None:
        """ASCII 回退 (终端无 unicode) 下渲染不得含 emoji/宽字符。"""
        from zall.cli import render as _r
        from zall.cli.tui.widgets import HelpOverlay

        _r.use_ascii_glyphs()  # 切换全局回退标志 (CJK 环境测试后恢复)
        try:
            h = HelpOverlay()
            h.set_rows([("ctrl+o", "Editor"), ("esc", "interrupt / close")])
            text = h.render().plain
            assert not re.search(r"[\U0001F000-\U0001FAFF]", text), f"emoji 泄漏: {text!r}"
            assert "\u276f" not in text  # 输入提示箭头也是宽字符, 回退后不得出现
        finally:
            _r.use_unicode_glyphs()

    def test_overlay_empty_rows_renders_blank(self) -> None:
        """反例: 未注入数据时渲染不得崩溃 (空面板安全性)。"""
        from zall.cli.tui.widgets import HelpOverlay

        assert HelpOverlay().render().plain == ""


# type: ignore[attr-defined]