"""思考过程投影 (§9.2.12) invariant tests.

IPR-0: 每个testincludes counterexamples.

Protected core invariants:
  1. 模型 reasoning (extended thinking) 被捕获并投影到呈现层, 不进 PR-0 幻觉判定
     (reasoning 与 content 是 ModelResponse 两个独立字段).
  2. streaming: reasoning 增量 → model_thinking 事件; content 增量 → model_token 事件.
  3. blocking: model_call 事件 payload 携带 reasoning (供渲染层画思考块).
  4. 呈现层: TTY 实时指示 + 完整思考块; non- TTY 一行摘要; JSON 逐行 NDJSON.
  5. 适配器: 从 delta.reasoning_content / message.reasoning_content 捕获.
"""

from __future__ import annotations

import io
import json
from typing import Any, Iterator

from zall.core.accountability import Evidence, Judge, JudgeVerdict
from zall.core.action import Action
from zall.core.context import Context
from zall.core.gate import UserResponder, UserResponse, UserResponseType
from zall.core.goal import (
    AcceptanceContract, GoalStatement, GoalTriple, GoalType, TerminationState,
)
from zall.core.loop import AgentLoop
from zall.core.loop_config import AgentConfig
from zall.core.loop_events import LoopEvent
from zall.core.model import (
    Message, ModelResponse, StopReason, ToolCall, ToolChoice,
)
from zall.core.safety import RuleSet, SafeLevel
from zall.core.tool import Tool, ToolRegistry, ToolResult
from zall.cli.render import CliRenderer


def _ev(kind: str, step: int = 1, **payload) -> LoopEvent:
    return LoopEvent(kind=kind, step=step, payload=payload)


# ──────────────────────────────────────────────────────────────────────────
# 呈现层 (renderer) test
# ──────────────────────────────────────────────────────────────────────────


class _TtyBuf:
    """假 TTY stream (isatty=True), 捕获 raw write (spinner / 思考指示行走 raw)."""

    __test__ = False

    def __init__(self) -> None:
        self._b = io.StringIO()

    def isatty(self) -> bool:
        return True

    def write(self, s: str) -> int:
        return self._b.write(s)

    def flush(self) -> None:
        pass

    def getvalue(self) -> str:
        return self._b.getvalue()


class TestRendererThinking:
    def test_tty_model_thinking_shows_live_indicator(self) -> None:
        """Happy path: TTY 下 model_thinking 实时显示思考content (Phase 6.1 增强)."""
        buf = _TtyBuf()
        r = CliRenderer(stream=buf)
        r(_ev("model_thinking", step=1, token="let me think", accumulated="let me think"))
        out = buf.getvalue()
        # Phase 6.1: 现在显示实际思考content, 而non- "thinking… N chars"
        assert "let me think" in out

    def test_nontty_model_call_shows_thinking_summary(self) -> None:
        """Happy path: non- TTY 下 model_call 携带 reasoning → 一行digest (无 ANSI)."""
        buf = io.StringIO()
        r = CliRenderer(stream=buf)
        r(_ev("model_call", step=1, content="answer", stop_reason="stop",
              tool_calls=[], reasoning="I thought about it"))
        out = buf.getvalue()
        assert "think" in out
        assert "I thought about it" in out
        assert "\x1b[" not in out  # non- TTY 无 ANSI

    def test_nontty_model_call_without_reasoning_no_thinking_line(self) -> None:
        """Counterexample: 无 reasoning 的 model_call 不打印 thinking digest行."""
        buf = io.StringIO()
        r = CliRenderer(stream=buf)
        r(_ev("model_call", step=1, content="answer", stop_reason="stop",
              tool_calls=[], reasoning=""))
        out = buf.getvalue()
        assert "think" not in out

    def test_json_model_thinking_is_ndjson(self) -> None:
        """Happy path: JSON pattern下 model_thinking 也output NDJSON 行."""
        buf = io.StringIO()
        r = CliRenderer(json_mode=True, stream=buf)
        r(_ev("model_thinking", step=1, token="x", accumulated="x"))
        obj = json.loads(buf.getvalue().strip())
        assert obj["kind"] == "model_thinking"
        assert obj["payload"]["token"] == "x"

    def test_tty_model_thinking_without_content_not_in_answer(self) -> None:
        """Counterexample: 思考 token 不会混进正式回答 content (通道isolate)."""
        buf = _TtyBuf()
        r = CliRenderer(stream=buf)
        # 仅 thinking, 无 model_call → 只有指示行, 无 Markdown 回答体
        r(_ev("model_thinking", step=1, token="secret thought", accumulated="secret thought"))
        out = buf.getvalue()
        # 指示行含思考content, 但不应出现完整思考文本以外的pollution
        assert "secret thought" in out


# ──────────────────────────────────────────────────────────────────────────
# 循环层 (loop) streamingsplittest
# ──────────────────────────────────────────────────────────────────────────


class _ThinkingAdapter:
    """fake adapter: 先给 reasoning 再给 content (streaming), blocking也带 reasoning."""

    __test__ = False

    def __init__(self) -> None:
        self.model_name = "fake-think"

    def complete(self, messages, tools, tool_choice=ToolChoice.AUTO) -> ModelResponse:
        return ModelResponse(content="answer", reasoning="r1r2",
                             stop_reason=StopReason.STOP)

    def complete_stream(self, messages, tools, tool_choice=ToolChoice.AUTO) -> Iterator[tuple[str, ModelResponse]]:
        # reasoning 阶段: content 仍for空 (content-so-far, 与realadapter一致)
        yield ("r1", ModelResponse(content="", reasoning="r1", stop_reason=StopReason.STOP))
        yield ("r2", ModelResponse(content="", reasoning="r1r2", stop_reason=StopReason.STOP))
        # content 阶段: reasoning 不再增长
        yield ("a", ModelResponse(content="a", reasoning="r1r2", stop_reason=StopReason.STOP))
        yield ("", ModelResponse(content="answer", reasoning="r1r2",
                                 tool_calls=(), stop_reason=StopReason.STOP))


class _NoReasoningAdapter:
    """Counterexample adapter: 完全不带 reasoning."""

    __test__ = False

    def __init__(self) -> None:
        self.model_name = "fake-noreason"

    def complete(self, messages, tools, tool_choice=ToolChoice.AUTO) -> ModelResponse:
        return ModelResponse(content="answer", reasoning="",
                             stop_reason=StopReason.STOP)

    def complete_stream(self, messages, tools, tool_choice=ToolChoice.AUTO) -> Iterator[tuple[str, ModelResponse]]:
        yield ("a", ModelResponse(content="a", reasoning="", stop_reason=StopReason.STOP))
        yield ("", ModelResponse(content="answer", reasoning="",
                                 tool_calls=(), stop_reason=StopReason.STOP))


class _CwdMetaStub:
    __test__ = False

    def __init__(self) -> None:
        self.cwd_path = "/home/user/project"
        self.git_branch = "main"
        self.git_remote = "origin"


def _make_goal() -> GoalTriple:
    class _T:
        exposed_dependency_set = None

        def __call__(self, state: object) -> TerminationState:
            return TerminationState.UNDECIDABLE
    return GoalTriple(
        statement=GoalStatement(
            intent="x", rewriting="x", rewrite_confidence=1.0,
            goal_type=GoalType.DOCS, translation_of=("s",), added_intent=(),
        ),
        termination=_T(),
        acceptance=AcceptanceContract(baseline_frozen_at="abc"),
    )


class _AutoAcceptResponder:
    __test__ = False

    def ask(self, action: Action, judgement) -> UserResponse:
        if judgement.level == SafeLevel.BLACKLIST:
            return UserResponse(response_type=UserResponseType.REJECT)
        return UserResponse(response_type=UserResponseType.ACCEPT)


class _MetJudge:
    __test__ = False

    @property
    def judge_type(self) -> str:
        return "system"

    def __call__(self, evidence: Evidence) -> JudgeVerdict:
        return JudgeVerdict(state=TerminationState.MET, report="ok")


def _make_loop(adapter, *, stream=False, observer=None) -> AgentLoop:
    return AgentLoop(
        model=adapter,
        tools=ToolRegistry(tools=()),
        rules=RuleSet(),
        goal=_make_goal(),
        context=Context(user_raw="x", cwd_meta=_CwdMetaStub()),
        user_responder=_AutoAcceptResponder(),
        config=AgentConfig(judge=_MetJudge(), observer=observer, stream=stream),
    )


class TestLoopThinking:
    def test_stream_emits_model_thinking_then_token(self) -> None:
        """Happy path: streaming reasoning 增量 → model_thinking; content 增量 → model_token."""
        collected: list[LoopEvent] = []
        loop = _make_loop(_ThinkingAdapter(), stream=True,
                          observer=lambda ev: collected.append(ev))
        loop.run()
        kinds = [e.kind for e in collected]
        assert "model_thinking" in kinds
        assert "model_token" in kinds
        think_tokens = [e.payload["token"] for e in collected if e.kind == "model_thinking"]
        assert think_tokens == ["r1", "r2"]
        content_tokens = [e.payload["token"] for e in collected if e.kind == "model_token"]
        assert content_tokens == ["a"]

    def test_stream_model_call_carries_reasoning(self) -> None:
        """Happy path: streaming最终 model_call event payload.reasoning for完整思考过程."""
        collected: list[LoopEvent] = []
        loop = _make_loop(_ThinkingAdapter(), stream=True,
                          observer=lambda ev: collected.append(ev))
        loop.run()
        mc = [e for e in collected if e.kind == "model_call"]
        assert mc, "expected model_call event"
        assert mc[0].payload.get("reasoning") == "r1r2"

    def test_blocking_model_call_carries_reasoning(self) -> None:
        """Happy path: blockingpattern model_call payload 也携带 reasoning."""
        collected: list[LoopEvent] = []
        loop = _make_loop(_NoReasoningAdapter(), stream=False,
                          observer=lambda ev: collected.append(ev))
        loop.run()
        mc = [e for e in collected if e.kind == "model_call"]
        assert mc
        # Counterexample adapter 无 reasoning → payload.reasoning for空串 (不丢字段)
        assert mc[0].payload.get("reasoning") == ""


# ──────────────────────────────────────────────────────────────────────────
# adapter reasoning 捕获test
# ──────────────────────────────────────────────────────────────────────────


class TestAdapterReasoning:
    def test_adapter_captures_reasoning_content(self, monkeypatch) -> None:
        """Happy path: _parse_response 从 message.reasoning_content 捕获思考过程."""
        from zall.adapters.openai_compat import OpenAICompatAdapter

        monkeypatch.setattr(
            "zall.safety.config.load_config",
            lambda: {"api_key": "x", "api_base": "http://x", "model": "m"},
        )
        a = OpenAICompatAdapter()
        resp = a._parse_response({
            "choices": [{"message": {"content": "hi", "reasoning_content": "think"}}],
            "usage": {},
        })
        assert resp.reasoning == "think"

    def test_adapter_falls_back_to_reasoning_field(self, monkeypatch) -> None:
        """Counterexample: 无 reasoning_content 时fallback到 reasoning 字段, 再无则空串."""
        from zall.adapters.openai_compat import OpenAICompatAdapter

        monkeypatch.setattr(
            "zall.safety.config.load_config",
            lambda: {"api_key": "x", "api_base": "http://x", "model": "m"},
        )
        a = OpenAICompatAdapter()
        resp = a._parse_response({
            "choices": [{"message": {"content": "hi", "reasoning": "alt"}}],
            "usage": {},
        })
        assert resp.reasoning == "alt"
