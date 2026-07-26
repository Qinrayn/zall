"""LiveRegion 活跃区 + flush 固化语义 test (Part G1).

kimi 流式固化范式的 Textual 落地: 进行中单块在活跃区跳动 (O(1) refresh),
完成即 flush 到历史一次。covers:
  1. LiveRegion (未挂载可测): set_message/is_active/clear/render + 9-role 渲染
  2. _flush_live: assistant/thinking/tool 固化到历史一次; 空块不固化; interrupted 标记
  3. _finalize_thinking: 活跃思考块固化; 无思考块时 no-op

IPR-0: 每个 test 含 counterexample。
"""

from __future__ import annotations

from zall.cli.tui import TuiApp
from zall.cli.tui.widgets import ChatMessage, LiveRegion, MessageList


# ──────────────────────────────────────────────────────────────────────────
# LiveRegion widget — 未挂载可测
# ──────────────────────────────────────────────────────────────────────────


class TestLiveRegionWidget:
    def test_empty_is_inactive(self) -> None:
        """Counterexample: 新建 LiveRegion 无块 → is_active False, message None, render 空。"""
        lr = LiveRegion()
        assert lr.is_active is False
        assert lr.message is None
        assert lr.render().plain == ""

    def test_set_message_activates(self) -> None:
        """Happy path: set_message → is_active True, 持有该块。"""
        lr = LiveRegion()
        m = ChatMessage(role="assistant", streaming=True)
        m._streaming_content = "hi"
        lr.set_message(m)
        assert lr.is_active is True
        assert lr.message is m

    def test_render_reuses_chatmessage(self) -> None:
        """Happy path: render 复用 ChatMessage.to_rich (活跃块与历史块观感一致)。"""
        lr = LiveRegion()
        m = ChatMessage(role="assistant", streaming=True)
        m._streaming_content = "hello world"
        lr.set_message(m)
        # 渲染出的内容应含流式文本 (markdown 渲染 → plain 含 'hello world')
        rendered = lr.render()
        # to_rich 返回 Markdown/Group/Text; 统一转成字符串检查
        from rich.console import Console
        import io
        buf = io.StringIO()
        Console(file=buf, width=80).print(rendered)
        assert "hello world" in buf.getvalue()

    def test_clear_deactivates(self) -> None:
        """Happy path: clear → is_active False, message None (块已固化后调用)。"""
        lr = LiveRegion()
        lr.set_message(ChatMessage(role="thinking", thinking="x"))
        lr.clear()
        assert lr.is_active is False
        assert lr.message is None

    def test_set_none_hides(self) -> None:
        """Counterexample: set_message(None) → 隐藏 (等价 clear)。"""
        lr = LiveRegion()
        lr.set_message(ChatMessage(role="tool", tool_id="bash"))
        lr.set_message(None)
        assert lr.is_active is False

    def test_active_tool_renders_without_crash(self) -> None:
        """Happy path: 执行中工具块 (tool_success=None) 渲染不崩 (蓝点分支)。"""
        lr = LiveRegion()
        lr.set_message(ChatMessage(role="tool", tool_id="bash", tool_args={"command": "ls"}))
        assert lr.render() is not None


# ──────────────────────────────────────────────────────────────────────────
# _flush_live — 固化到历史一次 (注入 live, 无需挂载)
# ──────────────────────────────────────────────────────────────────────────


class TestFlushLive:
    def test_flush_assistant_to_history_once(self) -> None:
        """Happy path: assistant 流式块 → 固化到历史一次, 活跃区清空, streaming 关。"""
        app = TuiApp()
        live = LiveRegion()
        m = ChatMessage(role="assistant", streaming=True)
        m._streaming_content = "final answer"
        live.set_message(m)
        history = MessageList()
        app._flush_live(history, live=live)
        assert len(history._messages) == 1          # 只追加一次
        assert history.last_message is m
        assert history.last_message.content == "final answer"
        assert history.last_message.streaming is False
        assert live.is_active is False

    def test_flush_empty_assistant_not_committed(self) -> None:
        """Counterexample: 空 assistant 块 → 不固化 (无空气泡)。"""
        app = TuiApp()
        live = LiveRegion()
        live.set_message(ChatMessage(role="assistant", streaming=True))  # 无内容
        history = MessageList()
        app._flush_live(history, live=live)
        assert len(history._messages) == 0
        assert live.is_active is False

    def test_flush_interrupted_appends_marker(self) -> None:
        """Happy path: interrupted=True → assistant 内容追加 [Interrupted] (保留 partial)。"""
        app = TuiApp()
        live = LiveRegion()
        m = ChatMessage(role="assistant", streaming=True)
        m._streaming_content = "partial"
        live.set_message(m)
        history = MessageList()
        app._flush_live(history, live=live, interrupted=True)
        assert "[Interrupted]" in history.last_message.content
        assert history.last_message.content.startswith("partial")

    def test_flush_interrupted_empty_no_marker(self) -> None:
        """Counterexample: 中断时空块 → 不固化, 也不产出孤立 [Interrupted]。"""
        app = TuiApp()
        live = LiveRegion()
        live.set_message(ChatMessage(role="assistant", streaming=True))
        history = MessageList()
        app._flush_live(history, live=live, interrupted=True)
        assert len(history._messages) == 0

    def test_flush_thinking_committed(self) -> None:
        """Happy path: thinking 块 → 固化到历史, thinking_streaming 关。"""
        app = TuiApp()
        live = LiveRegion()
        tm = ChatMessage(role="thinking", thinking="some reasoning")
        tm.thinking_streaming = True
        live.set_message(tm)
        history = MessageList()
        app._flush_live(history, live=live)
        assert history.last_message is tm
        assert tm.thinking_streaming is False

    def test_flush_empty_thinking_not_committed(self) -> None:
        """Counterexample: 空 thinking 块 → 不固化。"""
        app = TuiApp()
        live = LiveRegion()
        tm = ChatMessage(role="thinking", thinking="   ")  # 空白
        tm.thinking_streaming = True
        live.set_message(tm)
        history = MessageList()
        app._flush_live(history, live=live)
        assert len(history._messages) == 0

    def test_flush_tool_committed(self) -> None:
        """Happy path: 完成工具块 → 固化到历史一次。"""
        app = TuiApp()
        live = LiveRegion()
        live.set_message(ChatMessage(
            role="tool", tool_id="bash", tool_success=True, tool_output="ok",
        ))
        history = MessageList()
        app._flush_live(history, live=live)
        assert len(history._messages) == 1
        assert history.last_message.role == "tool"

    def test_flush_inactive_is_noop(self) -> None:
        """Counterexample: 活跃区为空时 flush → 无固化 (幂等)。"""
        app = TuiApp()
        live = LiveRegion()
        history = MessageList()
        app._flush_live(history, live=live)
        assert len(history._messages) == 0


# ──────────────────────────────────────────────────────────────────────────
# _finalize_thinking — 思考块固化
# ──────────────────────────────────────────────────────────────────────────


class TestFinalizeThinking:
    def test_finalize_active_thinking_commits(self) -> None:
        """Happy path: 活跃思考块 → 固化 + 清 active + thinking_streaming 关。"""
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
        assert history.last_message is tm
        assert live.is_active is False

    def test_finalize_no_thinking_is_noop(self) -> None:
        """Counterexample: 活跃区是 assistant (非 thinking) → 不固化它 (避免误吞)。"""
        app = TuiApp()
        live = LiveRegion()
        am = ChatMessage(role="assistant", streaming=True)
        am._streaming_content = "text"
        live.set_message(am)
        history = MessageList()
        app._finalize_thinking(history, live=live)
        assert len(history._messages) == 0   # assistant 未被 thinking-finalize 吞掉
        assert live.is_active is True         # 仍在活跃区
