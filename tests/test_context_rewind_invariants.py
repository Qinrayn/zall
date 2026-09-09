"""context_rewind (模型驱动上下文回滚, kimi D-Mail 对标) 不变量测试.

IPR-0: each test must contain a counterexample.

Protected invariants:
  I-REWIND-1: 工具未注册 → 不落锚, 消息列表无 [CHECKPOINT] 标记 (零成本默认)。
  I-REWIND-2: 信箱校验 — 越界 id / 空信 / 重复投递必须报错; 合法请求可取走一次。
  I-REWIND-3: e2e — 模型调用 context_rewind 后, 上下文截回锚点 + 追加"给过去
              自己的信"; 膨胀内容确实消失; timeline 记 CONTEXT_REWIND;
              doom-loop 追踪复位; 该锚点之后的锚点被丢弃。
  I-REWIND-4: 无效 checkpoint_id → 工具报错, 上下文不回滚 (反例孪生)。
"""

from __future__ import annotations

from typing import Any

import pytest

from zall.core.context import Context
from zall.core.gate import UserResponse, UserResponseType
from zall.core.goal import (
    AcceptanceContract,
    GoalStatement,
    GoalTriple,
    GoalType,
    TerminationState,
)
from zall.core.loop import AgentLoop
from zall.core.model import Message, ModelResponse, StopReason, ToolCall
from zall.core.rewind import RewindError, RewindMailbox
from zall.core.safety import RuleSet
from zall.core.tool import ToolRegistry, ToolResult
from zall.core.verifiability import EventType, RunRecorder
from zall.tools.context_rewind import ContextRewindTool


# ── fakes ──


class _ScriptedAdapter:
    __test__ = False

    def __init__(self, responses: list[ModelResponse]) -> None:
        self._responses = list(responses)
        self._i = 0
        self.seen_message_counts: list[int] = []

    @property
    def model_name(self) -> str:
        return "fake-scripted"

    def complete(self, messages, tools, tool_choice=None) -> ModelResponse:
        self.seen_message_counts.append(len(messages))
        if self._i >= len(self._responses):
            return ModelResponse(content="exhausted", stop_reason=StopReason.STOP)
        resp = self._responses[self._i]
        self._i += 1
        return resp


class _BigReadTool:
    """假读工具, 返回大块内容 (制造上下文膨胀)。"""

    __test__ = False
    tool_id = "fake_read"
    schema = {"type": "function", "function": {
        "name": "fake_read", "parameters": {"type": "object", "properties": {}}}}

    def execute(self, args: dict) -> ToolResult:
        return ToolResult(success=True, output="BLOAT " * 500)


class _Accept:
    __test__ = False

    def ask(self, action, judgement):
        return UserResponse(response_type=UserResponseType.ACCEPT)


class _UserTermination:
    exposed_dependency_set = None

    def __call__(self, state: object) -> TerminationState:
        return TerminationState.UNDECIDABLE


class _Cwd:
    cwd_path = "/tmp/p"
    git_branch = "main"
    git_remote = "origin"


def _make_goal() -> GoalTriple:
    return GoalTriple(
        statement=GoalStatement(
            intent="t", rewriting="t", rewrite_confidence=0.9,
            goal_type=GoalType.DOCS, translation_of=("seg1",), added_intent=(),
        ),
        termination=_UserTermination(),
        acceptance=AcceptanceContract(baseline_frozen_at="abc123"),
    )


def _make_loop(adapter, tools: tuple) -> AgentLoop:
    return AgentLoop(
        model=adapter,
        tools=ToolRegistry(tools=tools),
        rules=RuleSet(),
        goal=_make_goal(),
        context=Context(user_raw="t", cwd_meta=_Cwd()),
        user_responder=_Accept(),
    )


# ── I-REWIND-1: 未注册工具 → 零成本 ──


def test_no_anchor_without_tool() -> None:
    adapter = _ScriptedAdapter([
        ModelResponse(content="done", stop_reason=StopReason.STOP),
    ])
    loop = _make_loop(adapter, (_BigReadTool(),))
    loop.run(system_prompt="s")
    assert not any("[CHECKPOINT" in (m.content or "") for m in loop.messages)
    assert loop._rewind_anchors == []


def test_anchor_dropped_with_tool() -> None:
    """反例孪生: 注册工具后必须落锚。"""
    adapter = _ScriptedAdapter([
        ModelResponse(content="done", stop_reason=StopReason.STOP),
    ])
    loop = _make_loop(adapter, (_BigReadTool(), ContextRewindTool()))
    loop.run(system_prompt="s")
    markers = [m for m in loop.messages if "[CHECKPOINT" in (m.content or "")]
    assert markers, "context_rewind registered but no anchor dropped"
    assert loop._rewind_anchors


# ── I-REWIND-2: 信箱校验 ──


def test_mailbox_validation() -> None:
    box = RewindMailbox()
    box.set_n_anchors(2)
    with pytest.raises(RewindError):
        box.request(2, "msg")            # 越界 (只有 0/1)
    with pytest.raises(RewindError):
        box.request(-1, "msg")           # 负数
    with pytest.raises(RewindError):
        box.request(0, "   ")            # 空信
    box.request(1, "valid letter")       # 合法
    with pytest.raises(RewindError):
        box.request(0, "second")         # 单槽: 重复投递
    req = box.fetch()
    assert req is not None and req.checkpoint_id == 1
    assert box.fetch() is None           # 取走后为空


def test_mailbox_no_anchor_counterexample() -> None:
    box = RewindMailbox()
    with pytest.raises(RewindError, match="no checkpoints"):
        box.request(0, "msg")


# ── I-REWIND-3: e2e 回滚生效 ──


def test_rewind_folds_context_e2e() -> None:
    """步1 读大文件制造膨胀 → 步2 模型 rewind 到 CHECKPOINT 0 → 膨胀消失。"""
    adapter = _ScriptedAdapter([
        ModelResponse(content="", stop_reason=StopReason.TOOL_USE, tool_calls=(
            ToolCall(id="c1", tool_id="fake_read", args={}),)),
        ModelResponse(content="", stop_reason=StopReason.TOOL_USE, tool_calls=(
            ToolCall(id="c2", tool_id="context_rewind",
                     args={"checkpoint_id": 0,
                           "message": "file was irrelevant; proceed to write tests"}),)),
        ModelResponse(content="done", stop_reason=StopReason.STOP),
    ])
    tool = ContextRewindTool()
    loop = _make_loop(adapter, (_BigReadTool(), tool))
    loop._tool_seq_history.append("stale")  # 制造待复位的 doom-loop 痕迹
    loop.run(system_prompt="s")

    contents = [(m.role, m.content or "") for m in loop.messages]
    # 膨胀内容 (BLOAT 工具输出) 必须已被折叠掉
    assert not any("BLOAT" in c for _r, c in contents), "bloat survived the rewind"
    # "给过去自己的信" 必须存在且含原话
    assert any("proceed to write tests" in c for _r, c in contents)
    assert any("Context rewound to CHECKPOINT 0" in c for _r, c in contents)
    # timeline 记录 CONTEXT_REWIND
    kinds = [e.event_type for e in loop.recorder.events]
    assert EventType.CONTEXT_REWIND in kinds
    # doom-loop 追踪已复位 (回滚后至多含回滚之后的新序列)
    assert "stale" not in loop._tool_seq_history
    # 锚点收缩: 回滚到 0 后, 0 之后的锚点被丢弃 (随后步骤可再落新锚)
    assert loop._rewind_anchors[0] <= len(loop.messages)
    # 步3 模型看到的消息数必须小于步2 (上下文确实变小)
    assert adapter.seen_message_counts[2] < adapter.seen_message_counts[1]


# ── I-REWIND-4: 无效 id → 不回滚 (反例孪生) ──


def test_invalid_checkpoint_does_not_rewind() -> None:
    adapter = _ScriptedAdapter([
        ModelResponse(content="", stop_reason=StopReason.TOOL_USE, tool_calls=(
            ToolCall(id="c1", tool_id="fake_read", args={}),)),
        ModelResponse(content="", stop_reason=StopReason.TOOL_USE, tool_calls=(
            ToolCall(id="c2", tool_id="context_rewind",
                     args={"checkpoint_id": 99, "message": "bad id"}),)),
        ModelResponse(content="done", stop_reason=StopReason.STOP),
    ])
    loop = _make_loop(adapter, (_BigReadTool(), ContextRewindTool()))
    loop.run(system_prompt="s")
    contents = [m.content or "" for m in loop.messages]
    # 膨胀内容仍在 (未回滚)
    assert any("BLOAT" in c for c in contents)
    # 工具错误结果对模型可见
    assert any("context_rewind failed" in c for c in contents)
    # timeline 无 CONTEXT_REWIND
    kinds = [e.event_type for e in loop.recorder.events]
    assert EventType.CONTEXT_REWIND not in kinds


# ── I-REWIND-5: 跨 loop 隔离 (P1 回归: 进程级单例信箱) ──


def test_stale_pending_cleared_on_new_loop_bind() -> None:
    """上一 loop 被中断留下 pending → 新 loop 首绑必须清空 (防跨会话串味)。"""
    shared = ContextRewindTool()
    # 模拟被中断的 loop A: 留下了未取走的待处理回滚请求
    shared.mailbox.set_n_anchors(1)
    shared.mailbox.request(0, "letter from interrupted loop A")

    adapter = _ScriptedAdapter([
        ModelResponse(content="done", stop_reason=StopReason.STOP),
    ])
    loop = _make_loop(adapter, (shared,))  # loop B 绑定同一进程级工具
    loop.run(system_prompt="s")

    # 若无 reset: loop B 第一步后就会取走 A 的信并按 A 的锚点表截断 B 的上下文。
    # 有 reset: 信箱空, B 的上下文从未被折叠。
    contents = [m.content or "" for m in loop.messages]
    assert not any("Context rewound to CHECKPOINT" in c for c in contents), \
        "interrupted loop's rewind leaked into the new loop"
    assert not any("interrupted loop A" in c for c in contents)
    kinds = [e.event_type for e in loop.recorder.events]
    assert EventType.CONTEXT_REWIND not in kinds
    # 信箱 pending 已被清空 (A 的信消亡); B 自己重新建立了锚点计数
    assert shared.mailbox.fetch() is None
    assert shared.mailbox.n_anchors > 0
    assert shared.mailbox.n_anchors == len(loop._rewind_anchors)


def test_mailbox_reset_discards_request_and_anchors() -> None:
    """reset 同时清 pending 与锚点计数。"""
    box = RewindMailbox()
    box.set_n_anchors(3)
    box.request(1, "letter")
    box.reset()
    assert box.n_anchors == 0
    assert box.fetch() is None
    with pytest.raises(RewindError, match="no checkpoints"):
        box.request(0, "letter after reset")


# ── I-REWIND-6: 压缩啃掉锚点 → 拒绝且不吞信 (P2 回归) ─────


def test_refused_rewind_after_compaction_keeps_context() -> None:
    """锚点已被压缩啃掉 → 拒绝回滚, 信以显式拒绝说明保留 (不吞信)。"""
    from zall.core.loop_rewind import apply_pending_rewind
    recorder = RunRecorder("refused-test")
    tool = ContextRewindTool()

    class _StubLoop:
        __test__ = False

        def __init__(self) -> None:
            # 模拟"已绑定过该工具"的 loop (bind 时的 reset 只发生一次,
            # 后续读取直接走缓存, 不 reset pending — 否则测不到 refusal 路径)
            self._rewind_tool_checked = True
            self._rewind_tool = tool
            self._tools = ToolRegistry(tools=(tool,))
            self._rewind_anchors = [99]  # 锚点长度 99 — 实际消息只有 2 条 (被压缩啃掉)
            self._step_count = 0
            self._messages = [
                Message(role="system", content="system prompt"),
                Message.user("user input"),
            ]

        @property
        def messages(self) -> list[Message]:
            return self._messages

        def _append_message(self, message: Message) -> None:
            self._messages.append(message)

    loop = _StubLoop()
    loop._recorder = recorder
    tool.mailbox.set_n_anchors(1)
    tool.mailbox.request(0, "letter for a vanished anchor")

    applied = apply_pending_rewind(loop)
    assert applied is False
    contents = [m.content or "" for m in loop._messages]
    assert any("NOT applied" in c and "compacted" in c for c in contents), \
        "letter must be preserved with an explicit refusal"
    assert any("vanished anchor" in c for c in contents), "letter body must survive"
    # 原上下文完好: 没被截断, 也没有"给过去自己的信"模板
    assert not any("Context rewound to CHECKPOINT" in c for c in contents)
    # timeline 有审计痕迹 (refused=True)
    refused = [
        e for e in loop._recorder.events
        if e.event_type == EventType.CONTEXT_REWIND
        and e.payload.get("refused") is True
    ]
    assert refused, "refusal must be recorded for audit"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
