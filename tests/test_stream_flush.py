"""流式固化 end-to-end test (Part G1, 挂载 pilot 驱动 _handle_event).

验证 kimi 流式固化范式在 Textual 内的真实行为: 流式期间历史区 append-only 不被
逐 token 写入 (无 O(n²)); 完成/工具结束时才 flush 到历史一次。用 App.run_test()
挂载真实 widget 树驱动事件序列。

IPR-0: 每个 test 含 counterexample (断言流式期间历史 0 追加)。
"""

from __future__ import annotations

import asyncio

import pytest
pytest.importorskip("textual")

from zall.core.loop_events import LoopEvent
from zall.cli.tui.app import TuiApp
from zall.cli.tui.widgets import LiveRegion, MessageList


def _ev(kind: str, step: int = 1, **payload) -> LoopEvent:
    return LoopEvent(kind=kind, step=step, payload=payload)


def _run(coro) -> None:
    """在同步 test 内跑挂载协程 (无 pytest-asyncio, 用 asyncio.run)。"""
    asyncio.run(coro)


# ──────────────────────────────────────────────────────────────────────────
# 流式 assistant → 固化一次
# ──────────────────────────────────────────────────────────────────────────


class TestStreamingFlush:
    def test_tokens_do_not_touch_history_until_finalize(self) -> None:
        """核心 (反例): 流式期间历史 0 追加 (非逐 token 写入 → 无 O(n²)); 完成才追加一次。"""
        async def _t() -> None:
            app = TuiApp()
            async with app.run_test() as pilot:
                ml = app.query_one("#message-list", MessageList)
                live = app.query_one("#live-region", LiveRegion)
                base = len(ml._messages)

                app._handle_event(_ev("model_call_start", model="m"))
                for tok in ("Hel", "lo ", "wor", "ld"):
                    app._handle_event(_ev("model_token", token=tok))
                await pilot.pause()
                # 流式期间: 历史未被追加, 内容在活跃区
                assert len(ml._messages) - base == 0
                assert live.is_active is True
                assert live.message.role == "assistant"

                app._handle_event(_ev("model_call", content="", usage={"total": 10}))
                await pilot.pause()
                # 完成: 恰好追加一次, 活跃区清空
                assert len(ml._messages) - base == 1
                assert live.is_active is False
                assert ml.last_message.role == "assistant"
                assert ml.last_message.content == "Hello world"

        _run(_t())

    def test_streaming_content_accumulates(self) -> None:
        """Happy path: 多 token 累积为完整内容 (固化后 content 完整)。"""
        async def _t() -> None:
            app = TuiApp()
            async with app.run_test():
                ml = app.query_one("#message-list", MessageList)
                app._handle_event(_ev("model_call_start"))
                app._handle_event(_ev("model_token", token="foo"))
                app._handle_event(_ev("model_token", token="bar"))
                app._handle_event(_ev("model_call", content=""))
                assert ml.last_message.content == "foobar"
        _run(_t())


# ──────────────────────────────────────────────────────────────────────────
# 流式 thinking → 固化一次
# ──────────────────────────────────────────────────────────────────────────


class TestThinkingFlush:
    def test_thinking_streams_in_live_then_flushes(self) -> None:
        """Happy path: thinking 流式在活跃区; 非 thinking 事件到来时固化一次到历史。"""
        async def _t() -> None:
            app = TuiApp()
            async with app.run_test() as pilot:
                ml = app.query_one("#message-list", MessageList)
                live = app.query_one("#live-region", LiveRegion)
                base = len(ml._messages)

                app._handle_event(_ev("model_call_start"))
                app._handle_event(_ev("model_thinking", token="pondering"))
                await pilot.pause()
                assert len(ml._messages) - base == 0       # 未入历史
                assert live.is_active and live.message.role == "thinking"

                # 非 thinking 事件 (assistant token) → 顶部 guard 固化思考, 再开 assistant 流
                app._handle_event(_ev("model_token", token="answer"))
                await pilot.pause()
                assert len(ml._messages) - base == 1       # 思考固化一次
                assert ml.last_message.role == "thinking"
                assert live.message.role == "assistant"    # 活跃区换成 assistant
        _run(_t())


# ──────────────────────────────────────────────────────────────────────────
# 工具: 活跃区 active → 历史 result
# ──────────────────────────────────────────────────────────────────────────


class TestToolFlush:
    def test_tool_start_live_end_history(self) -> None:
        """Happy path: tool_call_start → 活跃区执行中块 (不入历史); tool_call_end → 历史一次。"""
        async def _t() -> None:
            app = TuiApp()
            async with app.run_test() as pilot:
                ml = app.query_one("#message-list", MessageList)
                live = app.query_one("#live-region", LiveRegion)
                base = len(ml._messages)

                app._handle_event(_ev("tool_call_start", tool_id="bash", args={"command": "ls"}))
                await pilot.pause()
                assert len(ml._messages) - base == 0       # 执行中不入历史
                assert live.is_active and live.message.role == "tool"
                assert live.message.tool_success is None    # 执行中 (蓝点)

                app._handle_event(_ev("tool_call_end", tool_id="bash", success=True, output="f1\nf2"))
                await pilot.pause()
                assert len(ml._messages) - base == 1       # 结果固化一次
                assert live.is_active is False
                assert ml.last_message.role == "tool"
                assert ml.last_message.tool_success is True
                assert app._tool_calls_in_step == 1
        _run(_t())

    def test_model_tool_call_does_not_spam_history(self) -> None:
        """Counterexample: model_tool_call (流式预览) 不入历史 (去噪, 由 start/end 呈现)。"""
        async def _t() -> None:
            app = TuiApp()
            async with app.run_test() as pilot:
                ml = app.query_one("#message-list", MessageList)
                base = len(ml._messages)
                app._handle_event(_ev("model_tool_call", tool_calls=[
                    {"tool_id": "bash", "args": {"command": "ls"}},
                ]))
                await pilot.pause()
                assert len(ml._messages) - base == 0       # 预览不入历史
        _run(_t())


# ──────────────────────────────────────────────────────────────────────────
# 中断: 活跃区 partial → 固化 + 标记
# ──────────────────────────────────────────────────────────────────────────


class TestInterruptFlush:
    def test_interrupt_preserves_partial(self) -> None:
        """Happy path: 流式中中断 → 活跃区 partial 固化 + [Interrupted] 标记, 活跃区清空。"""
        async def _t() -> None:
            app = TuiApp()
            async with app.run_test() as pilot:
                ml = app.query_one("#message-list", MessageList)
                live = app.query_one("#live-region", LiveRegion)
                app._handle_event(_ev("model_call_start"))
                app._handle_event(_ev("model_token", token="half done"))
                await pilot.pause()
                app._show_interrupt()
                await pilot.pause()
                # partial + 中断标记 固化; 活跃区清空
                assert live.is_active is False
                joined = " ".join(m.content for m in ml._messages if m.role == "assistant")
                assert "half done" in joined
                assert "[Interrupted]" in joined
        _run(_t())
