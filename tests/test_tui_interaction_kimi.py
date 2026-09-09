"""kimi-parity TUI 交互: Shift+Tab 切 plan 模式 + Ctrl+O 外部编辑器。

学 kimi-cli 的交互快捷键 (docs/en/reference/keyboard.md):
  - Shift+Tab → 切换 plan 模式 (只读探索/规划)
  - Ctrl+O    → 外部编辑器编辑输入 ($VISUAL/$EDITOR)

不变量 (含反例):
  - plan 切换是对合 (involution): 两次 = 回到原态; status_mode 徽章同步。
  - 已有 loop 时切换立即 set_plan_mode; 无 loop 也不崩 (state 仍更新)。
  - _detect_editor 优先 $VISUAL 再 $EDITOR, 支持多词命令 (code --wait);
    两者皆缺且平台无编辑器 → None (反例)。
  - 键位绑定与消息管线齐备 (Footer 可提示; TextArea 聚焦时也生效)。
"""

from __future__ import annotations

import shutil

import pytest
pytest.importorskip("textual")


# ──────────────────────────────────────────────────────────────────────────
# 1. Shift+Tab — plan 模式切换
# ──────────────────────────────────────────────────────────────────────────


class TestPlanToggle:
    def test_toggle_plan_flips_state_and_badge(self) -> None:
        from zall.cli.tui import TuiApp
        app = TuiApp()
        assert app._state.get("plan_mode", False) is False
        app.action_toggle_plan()
        assert app._state["plan_mode"] is True
        assert app.status_mode == "plan"

    def test_toggle_plan_is_involution(self) -> None:
        """反例: 两次切换必须回到 off (不是累加)。"""
        from zall.cli.tui import TuiApp
        app = TuiApp()
        app.action_toggle_plan()
        app.action_toggle_plan()
        assert app._state["plan_mode"] is False
        assert app.status_mode == ""

    def test_toggle_plan_applies_to_existing_loop(self) -> None:
        from unittest.mock import MagicMock
        from zall.cli.tui import TuiApp
        app = TuiApp()
        loop = MagicMock()
        app._agent_loop = loop
        app.action_toggle_plan()
        loop.set_plan_mode.assert_called_once_with(True)

    def test_toggle_plan_no_loop_does_not_crash(self) -> None:
        """反例: 无 loop 时不得崩溃, state 仍更新。"""
        from zall.cli.tui import TuiApp
        app = TuiApp()
        app._agent_loop = None
        app.action_toggle_plan()
        assert app._state["plan_mode"] is True


# ──────────────────────────────────────────────────────────────────────────
# 2. Ctrl+O — 外部编辑器探测
# ──────────────────────────────────────────────────────────────────────────


class TestExternalEditorDetect:
    def test_prefers_visual_over_editor(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from zall.cli.tui.app import _detect_editor
        monkeypatch.setenv("VISUAL", "code --wait")
        monkeypatch.setenv("EDITOR", "vim")
        assert _detect_editor() == ["code", "--wait"]

    def test_falls_back_to_editor(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from zall.cli.tui.app import _detect_editor
        monkeypatch.delenv("VISUAL", raising=False)
        monkeypatch.setenv("EDITOR", "nano")
        assert _detect_editor() == ["nano"]

    def test_none_when_no_editor_available(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """反例: 无 env 且平台无编辑器 → None (调用方须提示用户)。"""
        from zall.cli.tui.app import _detect_editor
        monkeypatch.delenv("VISUAL", raising=False)
        monkeypatch.delenv("EDITOR", raising=False)
        monkeypatch.setattr(shutil, "which", lambda _c: None)
        assert _detect_editor() is None

    def test_action_external_editor_unmounted_is_safe(self) -> None:
        """未挂载 app 时 query_one 抛错应静默返回, 不崩。"""
        from zall.cli.tui import TuiApp
        app = TuiApp()
        app.action_external_editor()  # must not raise


# ──────────────────────────────────────────────────────────────────────────
# 3. 键位绑定 + 消息管线
# ──────────────────────────────────────────────────────────────────────────


class TestBindingsAndWiring:
    def test_bindings_include_new_shortcuts(self) -> None:
        from zall.cli.tui import TuiApp
        keys = {b.key for b in TuiApp.BINDINGS}
        assert "shift+tab" in keys
        assert "ctrl+o" in keys

    def test_message_classes_exist(self) -> None:
        from zall.cli.tui.widgets import ChatTextArea, InputBar
        assert hasattr(ChatTextArea, "TogglePlan")
        assert hasattr(ChatTextArea, "OpenEditor")
        assert hasattr(InputBar, "TogglePlan")
        assert hasattr(InputBar, "OpenEditor")

    def test_inputbar_reposts_toggle_plan(self) -> None:
        """ChatTextArea.TogglePlan → InputBar 重投 InputBar.TogglePlan。"""
        from unittest.mock import MagicMock
        from zall.cli.tui.widgets import ChatTextArea, InputBar
        bar = InputBar()
        posted: list[object] = []
        bar.post_message = lambda m: posted.append(m)  # type: ignore[assignment]
        bar.on_chat_text_area_toggle_plan(MagicMock())
        assert any(isinstance(m, InputBar.TogglePlan) for m in posted)

    def test_inputbar_reposts_open_editor(self) -> None:
        from unittest.mock import MagicMock
        from zall.cli.tui.widgets import InputBar
        bar = InputBar()
        posted: list[object] = []
        bar.post_message = lambda m: posted.append(m)  # type: ignore[assignment]
        bar.on_chat_text_area_open_editor(MagicMock())
        assert any(isinstance(m, InputBar.OpenEditor) for m in posted)


# ─────────────────────────────────────────────────────────────
# 4. Ctrl+S steer + Enter 队列 (A/B)
# ─────────────────────────────────────────────────────────────


class TestSteerAndQueue:
    def test_enqueue_updates_count(self) -> None:
        from zall.cli.tui import TuiApp
        app = TuiApp()
        app._agent_running = True
        app._enqueue_pending("a")
        app._enqueue_pending("b")
        assert app.status_queued == 2

    def test_pop_pending_fifo(self) -> None:
        from zall.cli.tui import TuiApp
        app = TuiApp()
        app._enqueue_pending("first")
        app._enqueue_pending("second")
        assert app._pop_pending() == "first"
        assert app._pop_pending() == "second"
        assert app._pop_pending() is None  # 反例: 空队列返回 None

    def test_steer_push_pop_clears(self) -> None:
        from zall.cli.tui import TuiApp
        app = TuiApp()
        app._push_steer("x")
        app._push_steer("y")
        assert app._pop_steer_messages() == ["x", "y"]
        assert app._pop_steer_messages() == []  # 反例: drain 后为空

    def test_do_steer_while_running_enqueues_steer(self) -> None:
        from zall.cli.tui import TuiApp
        app = TuiApp()
        app._agent_running = True
        app._do_steer("hurry up")
        assert app._pop_steer_messages() == ["hurry up"]

    def test_empty_steer_promotes_oldest_pending(self) -> None:
        """反例: 空 Ctrl+S 时, 把最早排队消息转成 steer (kimi 行为)。"""
        from zall.cli.tui import TuiApp
        app = TuiApp()
        app._agent_running = True
        app._enqueue_pending("queued-1")
        app._do_steer("")
        assert app._pop_steer_messages() == ["queued-1"]
        assert app.status_queued == 0

    def test_do_steer_idle_does_not_steer(self) -> None:
        """反例: 无活动回合 + 空文本 → 不崩且不进 steer 队列。"""
        from zall.cli.tui import TuiApp
        app = TuiApp()
        app._agent_running = False
        app._do_steer("")
        assert app._pop_steer_messages() == []

    def test_enter_while_running_steers_not_queues(self) -> None:
        """真实使用反馈: 运行中按 Enter 必须立即 steer 注入当前回合。

        反例孪生: 若回归到旧行为 (进 pending 队列等回合结束),
        steer 队列为空断言失败 — 慢端点下排队等于回车无反应。
        """
        from types import SimpleNamespace
        from zall.cli.tui import TuiApp
        from zall.cli.tui.widgets import InputBar
        app = TuiApp()
        app._agent_running = True
        app._cached_msg_list = SimpleNamespace(add_message=lambda m: None)
        app.on_input_bar_submitted(InputBar.Submitted("do it now"))
        assert app._pop_steer_messages() == ["do it now"]
        assert app._pop_pending() is None  # 不再默默排队

    def test_steer_bindings_and_messages(self) -> None:
        from zall.cli.tui import TuiApp
        from zall.cli.tui.widgets import ChatTextArea, InputBar
        assert "ctrl+s" in {b.key for b in TuiApp.BINDINGS}
        assert hasattr(ChatTextArea, "Steer")
        assert hasattr(InputBar, "Steer")

    def test_inputbar_reposts_steer_with_text(self) -> None:
        from unittest.mock import MagicMock
        from zall.cli.tui.widgets import InputBar
        bar = InputBar()
        bar._textarea.text = "steer this"
        posted: list[object] = []
        bar.post_message = lambda m: posted.append(m)  # type: ignore[assignment]
        bar.on_chat_text_area_steer(MagicMock())
        steers = [m for m in posted if isinstance(m, InputBar.Steer)]
        assert steers and steers[0].text == "steer this"


# ─────────────────────────────────────────────────────────────
# 5. @ 文件补全 (C)
# ─────────────────────────────────────────────────────────────


class TestFileCompletion:
    def test_file_query_extraction(self) -> None:
        from zall.cli.tui.widgets import InputBar
        assert InputBar._file_query("explain @src/za") == "src/za"
        assert InputBar._file_query("@foo") == "foo"
        assert InputBar._file_query("just text") is None       # 反例: 无 @
        assert InputBar._file_query("mail a@b.com") is None     # 反例: @ 前非空白
        assert InputBar._file_query("@a b") is None             # 反例: @token 后有空格

    def test_workspace_matches_rank_prefix_first(self) -> None:
        import os
        from zall.cli.tui.widgets import InputBar
        import zall.cli.file_complete as fc
        # v2.x: @ 补全已抽到共享 file_complete (模块级缓存, 按 cwd 键)。
        fc._CACHE[os.getcwd()] = [
            "docs/loop.md", "src/zall/core/loop.py", "loop.txt", "a/b/c.py",
        ]
        try:
            bar = InputBar()
            res = bar._workspace_file_matches("loop", limit=8)
            assert res[0] == "loop.txt"        # basename 前缀匹配 + 路径最短
            assert "a/b/c.py" not in res        # 反例: 不匹配的被过滤
        finally:
            fc.clear_cache()

    def test_complete_file_selection_replaces_at_token(self) -> None:
        from zall.cli.tui.widgets import InputBar
        bar = InputBar()
        bar._textarea.text = "explain @src/za"
        bar._menu_kind = "file"
        bar._menu.update_items([("src/zall/core/loop.py", "")], prefix="@")
        bar._complete_menu_selection()
        assert bar._textarea.text == "explain @src/zall/core/loop.py "

    def test_command_menu_prefix(self) -> None:
        from zall.cli.tui.widgets import CommandMenu
        m = CommandMenu()
        m.update_items([("model", "switch model")], prefix="/")
        assert m._prefix == "/"
        m.update_items([("src/x.py", "")], prefix="@")
        assert m._prefix == "@"


# ─────────────────────────────────────────────────────────────
# 6. bug 修复回归 (本轮猎杀)
# ─────────────────────────────────────────────────────────────


class TestBugFixes:
    def test_completion_moves_cursor_to_end_file(self) -> None:
        """Bug1: 补全后光标应到行末 (原 cursor_position 是无效空操作)。"""
        from zall.cli.tui.widgets import InputBar
        b = InputBar()
        b._textarea.text = "explain @src/za"
        b._menu_kind = "file"
        b._menu.update_items([("src/zall/core/loop.py", "")], prefix="@")
        b._complete_menu_selection()
        assert b._textarea.text == "explain @src/zall/core/loop.py "
        assert b._textarea.cursor_location == b._textarea.document.end

    def test_completion_moves_cursor_to_end_command(self) -> None:
        from zall.cli.tui.widgets import InputBar
        b = InputBar()
        b._textarea.text = "/mod"
        b._menu_kind = "command"
        b._menu.update_items([("model", "switch model")], prefix="/")
        b._complete_menu_selection()
        assert b._textarea.text == "/model "
        assert b._textarea.cursor_location == b._textarea.document.end

    def test_finalize_thinking_locks_block(self) -> None:
        """思考定格 (LiveRegion flush): thinking_streaming→False + 清 active + 固化到历史一次。"""
        from zall.cli.tui import TuiApp
        from zall.cli.tui.widgets import ChatMessage, LiveRegion, MessageList
        app = TuiApp()
        app._thinking_active = True
        tm = ChatMessage(role="thinking", thinking="reasoning")
        tm.thinking_streaming = True
        live = LiveRegion()
        live.set_message(tm)
        history = MessageList()
        app._finalize_thinking(history, live=live)
        assert app._thinking_active is False
        assert tm.thinking_streaming is False
        assert live.is_active is False          # 活跃区已清空
        assert history.last_message is tm       # 思考块固化到历史

    def test_finalize_thinking_noop_when_inactive(self) -> None:
        """反例: 活跃区无思考块时 finalize 不固化 (幂等/无副作用)。"""
        from zall.cli.tui import TuiApp
        from zall.cli.tui.widgets import LiveRegion, MessageList
        app = TuiApp()
        app._thinking_active = False
        live = LiveRegion()  # 空活跃区
        history = MessageList()
        app._finalize_thinking(history, live=live)
        assert history.last_message is None      # 无固化


# ─────────────────────────────────────────────────────────────
# 7. 确认门 (TuiUserResponder) — P0: greylist 确认不再挂死
# ─────────────────────────────────────────────────────────────


class _ConfirmApp:
    """同步 fake app: begin_confirm 立即提交回答 (event 先于 wait 置位, 无线程依赖)。"""
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.responder = None
        self.panels: list = []

    def call_from_thread(self, fn, *a):
        return fn(*a)

    def show_confirm_request(self, tool_id, args, level) -> None:
        self.panels.append((tool_id, level))

    def begin_confirm(self, prompt) -> None:
        self.responder.submit_reply(self.reply)

    def begin_confirm_select(self, choices) -> None:
        # greylist 主选择走可选择菜单 seam: 立即提交预设回答 (模拟用户选中)。
        self.responder.submit_reply(self.reply)


class TestConfirmGate:
    def _ask(self, reply: str, level="greylist"):
        from zall.cli.tui.tui_responder import TuiUserResponder
        from zall.core.action import Action
        from zall.core.safety import Judgement, SafeLevel
        app = _ConfirmApp(reply)
        r = TuiUserResponder(app)
        app.responder = r
        lvl = SafeLevel.GREYLIST if level == "greylist" else SafeLevel.BLACKLIST
        act = Action(tool_id="write_file", args={"path": "x.py"})
        return r.ask(act, Judgement(level=lvl, matched_rule_ids=())), app, r

    def test_greylist_yes_accepts(self) -> None:
        from zall.core.gate import UserResponseType
        res, app, _ = self._ask("y")
        assert res.response_type == UserResponseType.ACCEPT
        assert app.panels and app.panels[0][1] == "greylist"

    def test_greylist_no_rejects(self) -> None:
        from zall.core.gate import UserResponseType
        res, _, _ = self._ask("n")
        assert res.response_type == UserResponseType.REJECT

    def test_greylist_always_accepts_and_persists(self) -> None:
        from zall.core.gate import UserResponseType
        res, _, r = self._ask("a")
        assert res.response_type == UserResponseType.ACCEPT
        assert "write_file" in r._session_allow  # 'a' → 本会话允许

    def test_cancel_unblocks_as_reject(self) -> None:
        """反例: 中断时 cancel() → 空回答 → reject (worker 不卡死)。"""
        from zall.cli.tui.tui_responder import TuiUserResponder
        from zall.core.gate import UserResponseType
        from zall.core.action import Action
        from zall.core.safety import Judgement, SafeLevel

        class _CancelApp(_ConfirmApp):
            def begin_confirm_select(self, choices):
                self.responder.cancel()  # 模拟中断 (greylist 主选择走 select seam)
        app = _CancelApp("")
        r = TuiUserResponder(app)
        app.responder = r
        res = r.ask(Action(tool_id="bash", args={"command": "rm x"}),
                    Judgement(level=SafeLevel.GREYLIST, matched_rule_ids=()))
        assert res.response_type == UserResponseType.REJECT
