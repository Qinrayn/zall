"""§9.2.9 Reactive auto-compact tests (v0.0.18).

Aligns with Claude Code / OpenCode long-session experience: when the model returns LENGTH (window full),
AgentLoop automatically compacts the model context and retries, instead of terminating with an error.

IPR-0: each test includes a counterexample. Core invariants:
  - compactor injected + LENGTH → compact and retry (success does not terminate)
  - no compactor → LENGTH terminates directly (legacy behavior, backward compatible)
  - compaction fails (compactor raises) → fail-safe, fall back to LENGTH termination, no crash
  - compact 0 items → no retry, terminate
  - still LENGTH after compaction → honest termination ("could not reduce further")
  - timeline full fidelity (§6.1): compaction recorded as CONTEXT_COMPACTION event
"""

from __future__ import annotations

from zall.core.action import Action
from zall.core.compactor import CompactResult, ModelCompactor
from zall.core.context import Context
from zall.core.gate import UserResponder, UserResponse, UserResponseType
from zall.core.goal import (
    AcceptanceContract,
    GoalStatement,
    GoalTriple,
    GoalType,
    TerminationState,
)
from zall.core.loop import AgentLoop
from zall.core.model import (
    Message,
    ModelResponse,
    StopReason,
    ToolChoice,
)
from zall.core.safety import RuleSet, SafeLevel
from zall.core.tool import ToolRegistry, ToolResult
from zall.core.verifiability import EventType


# ──────────────────────────────────────────────────────────────────────────
# Fakes
# ──────────────────────────────────────────────────────────────────────────


class _ScriptedAdapter:
    """Scripted adapter returning preset ModelResponses. Calls with tool_choice=NONE (compactor summary)
take a separate summary branch and do not consume the main script."""

    __test__ = False

    def __init__(self, responses: list[ModelResponse]) -> None:
        self._responses = list(responses)
        self._i = 0
        self.main_calls = 0

    @property
    def model_name(self) -> str:
        return "fake-scripted"

    def complete(
        self,
        messages: list[Message],
        tools: list[dict],
        tool_choice: ToolChoice = ToolChoice.AUTO,
    ) -> ModelResponse:
        if tool_choice == ToolChoice.NONE:
            # compactor internal summary call
            return ModelResponse(content="SUMMARY: work done", stop_reason=StopReason.STOP)
        self.main_calls += 1
        if self._i >= len(self._responses):
            return ModelResponse(content="exhausted", stop_reason=StopReason.STOP)
        resp = self._responses[self._i]
        self._i += 1
        return resp


class _EchoTool:
    __test__ = False

    @property
    def tool_id(self) -> str:
        return "echo"

    @property
    def schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": "echo",
                "description": "echo",
                "parameters": {"type": "object", "properties": {}},
            },
        }

    def execute(self, args: dict) -> ToolResult:
        return ToolResult(success=True, output="ok")


class _AutoAcceptResponder:
    __test__ = False

    def ask(self, action: Action, judgement) -> UserResponse:
        if judgement.level == SafeLevel.BLACKLIST:
            return UserResponse(response_type=UserResponseType.REJECT)
        return UserResponse(response_type=UserResponseType.ACCEPT)


class _FakeCompactor:
    """Controllable compaction strategy: reduce (real) / zero (no-op) / raise (exception)."""

    __test__ = False

    def __init__(self, mode: str = "reduce") -> None:
        self.mode = mode
        self.calls = 0

    def compact(self, messages, model) -> CompactResult:
        self.calls += 1
        if self.mode == "raise":
            raise RuntimeError("boom")
        if self.mode == "zero":
            return CompactResult(
                compressed_messages=list(messages),
                compacted_count=0,
                summary="(nothing)",
            )
        # reduce: keep only system + last message
        systems = [m for m in messages if m.role == "system"][:1]
        kept = systems + messages[-1:]
        return CompactResult(
            compressed_messages=kept,
            compacted_count=max(1, len(messages) - len(kept)),
            summary="summarized",
        )


class _CwdMetaStub:
    __test__ = False

    def __init__(self) -> None:
        self.cwd_path = "/home/user/project"
        self.git_branch = "main"
        self.git_remote = "origin"


def _make_goal() -> GoalTriple:
    class _UserTermination:
        exposed_dependency_set = None

        def __call__(self, state: object) -> TerminationState:
            return TerminationState.UNDECIDABLE

    return GoalTriple(
        statement=GoalStatement(
            intent="do x",
            rewriting="do x well",
            rewrite_confidence=0.9,
            goal_type=GoalType.DOCS,
            translation_of=("seg1",),
            added_intent=(),
        ),
        termination=_UserTermination(),
        acceptance=AcceptanceContract(baseline_frozen_at="abc123"),
    )


def _make_loop(adapter, compactor=None) -> AgentLoop:
    return AgentLoop(
        model=adapter,
        tools=ToolRegistry(tools=(_EchoTool(),)),
        rules=RuleSet(),
        goal=_make_goal(),
        context=Context(user_raw="do x", cwd_meta=_CwdMetaStub()),
        user_responder=_AutoAcceptResponder(),
        compactor=compactor,
    )


def _seed(loop: AgentLoop, n: int = 6) -> None:
    """Seed the loop with enough messages to trigger real compaction."""
    msgs: list[Message] = [Message(role="system", content="sys")]
    for i in range(n):
        role = "user" if i % 2 == 0 else "assistant"
        msgs.append(Message(role=role, content=f"m{i} " * 30))
    loop._messages = msgs


# ──────────────────────────────────────────────────────────────────────────
# Happy path: LENGTH triggers compaction and retry
# ──────────────────────────────────────────────────────────────────────────


class TestAutoCompactOnLength:
    def test_length_then_stop_compacts_and_retries(self) -> None:
        """LENGTH → compact → retry → STOP. Does not terminate; compactor is called once."""
        adapter = _ScriptedAdapter([
            ModelResponse(content="", stop_reason=StopReason.LENGTH),
            ModelResponse(content="done", stop_reason=StopReason.STOP),
        ])
        comp = _FakeCompactor(mode="reduce")
        loop = _make_loop(adapter, compactor=comp)
        _seed(loop)

        result = loop.step()

        # Counterexample: without auto-compact, step would be terminal; here should be awaiting_input
        assert result.kind == "awaiting_input"
        assert result.egress is None
        assert comp.calls == 1
        assert adapter.main_calls == 2  # first LENGTH + retry after compaction

    def test_context_compaction_recorded_in_timeline(self) -> None:
        """§6.1 full fidelity: compaction recorded as CONTEXT_COMPACTION event, chain intact."""
        adapter = _ScriptedAdapter([
            ModelResponse(content="", stop_reason=StopReason.LENGTH),
            ModelResponse(content="done", stop_reason=StopReason.STOP),
        ])
        loop = _make_loop(adapter, compactor=_FakeCompactor("reduce"))
        _seed(loop)
        loop.step()

        types = [e.event_type for e in loop.recorder.events]
        assert EventType.CONTEXT_COMPACTION in types
        assert loop.recorder.verify_chain() is True

        ev = next(e for e in loop.recorder.events
                  if e.event_type == EventType.CONTEXT_COMPACTION)
        assert ev.payload["reason"] == "model_length"
        assert ev.payload["compacted_count"] >= 1
        assert "strategy" in ev.payload

    def test_messages_actually_reduced(self) -> None:
        """压缩后 model 看到的 messages 变少 (window收缩)."""
        adapter = _ScriptedAdapter([
            ModelResponse(content="", stop_reason=StopReason.LENGTH),
            ModelResponse(content="done", stop_reason=StopReason.STOP),
        ])
        loop = _make_loop(adapter, compactor=_FakeCompactor("reduce"))
        _seed(loop, n=6)
        before = len(loop._messages)
        loop.step()
        assert len(loop._messages) < before

    def test_compaction_event_emitted_to_observer(self) -> None:
        """Compaction event emitted to observer (presentation layer projection)."""
        events: list = []
        adapter = _ScriptedAdapter([
            ModelResponse(content="", stop_reason=StopReason.LENGTH),
            ModelResponse(content="done", stop_reason=StopReason.STOP),
        ])
        loop = AgentLoop(
            model=adapter,
            tools=ToolRegistry(tools=(_EchoTool(),)),
            rules=RuleSet(),
            goal=_make_goal(),
            context=Context(user_raw="do x", cwd_meta=_CwdMetaStub()),
            user_responder=_AutoAcceptResponder(),
            observer=events.append,
            compactor=_FakeCompactor("reduce"),
        )
        _seed(loop)
        loop.step()
        kinds = [e.kind for e in events]
        assert "context_compaction" in kinds


# ──────────────────────────────────────────────────────────────────────────
# Counterexamples: no compactor / compaction fails / nothing to compact / still full after compact
# ──────────────────────────────────────────────────────────────────────────


class TestAutoCompactCounterExamples:
    def test_no_compactor_length_terminates(self) -> None:
        """Counterexample: no compactor injected → LENGTH terminates directly (legacy backward compatible)."""
        adapter = _ScriptedAdapter([
            ModelResponse(content="", stop_reason=StopReason.LENGTH),
        ])
        loop = _make_loop(adapter, compactor=None)
        _seed(loop)
        result = loop.step()

        assert result.kind == "terminal"
        assert result.egress is not None
        assert result.egress.final_state == TerminationState.UNDECIDABLE
        assert "LENGTH" in (result.egress.error or "")

    def test_compactor_failure_is_safe(self) -> None:
        """Counterexample: compactor raises → no crash, fall back to LENGTH termination + broadcast error."""
        events: list = []
        adapter = _ScriptedAdapter([
            ModelResponse(content="", stop_reason=StopReason.LENGTH),
        ])
        loop = AgentLoop(
            model=adapter,
            tools=ToolRegistry(tools=(_EchoTool(),)),
            rules=RuleSet(),
            goal=_make_goal(),
            context=Context(user_raw="do x", cwd_meta=_CwdMetaStub()),
            user_responder=_AutoAcceptResponder(),
            observer=events.append,
            compactor=_FakeCompactor("raise"),
        )
        _seed(loop)
        result = loop.step()

        assert result.kind == "terminal"  # no crash, honest termination
        assert result.egress.final_state == TerminationState.UNDECIDABLE
        # Compression failure broadcast as error event
        errs = [e for e in events if e.kind == "error"
                and "compaction failed" in str(e.payload.get("error", ""))]
        assert errs
        # timeline should NOT have CONTEXT_COMPACTION (compaction didn't succeed)
        types = [e.event_type for e in loop.recorder.events]
        assert EventType.CONTEXT_COMPACTION not in types

    def test_zero_compaction_terminates(self) -> None:
        """Counterexample: compact 0 items (nothing left to compress) → no retry, terminate."""
        adapter = _ScriptedAdapter([
            ModelResponse(content="", stop_reason=StopReason.LENGTH),
            ModelResponse(content="done", stop_reason=StopReason.STOP),
        ])
        comp = _FakeCompactor("zero")
        loop = _make_loop(adapter, compactor=comp)
        _seed(loop)
        result = loop.step()

        assert result.kind == "terminal"
        assert comp.calls == 1
        assert adapter.main_calls == 1  # no retry

    def test_still_length_after_compact_terminates(self) -> None:
        """Counterexample: compaction succeeds but retry still LENGTH → honest termination."""
        adapter = _ScriptedAdapter([
            ModelResponse(content="", stop_reason=StopReason.LENGTH),
            ModelResponse(content="", stop_reason=StopReason.LENGTH),
        ])
        comp = _FakeCompactor("reduce")
        loop = _make_loop(adapter, compactor=comp)
        _seed(loop)
        result = loop.step()

        assert result.kind == "terminal"
        assert result.egress.final_state == TerminationState.UNDECIDABLE
        assert "could not reduce" in (result.egress.error or "")
        assert comp.calls == 1
        assert adapter.main_calls == 2


# ──────────────────────────────────────────────────────────────────────────
# End-to-end: real ModelCompactor
# ──────────────────────────────────────────────────────────────────────────


class TestEndToEndModelCompactor:
    def test_real_compactor_length_recovery(self) -> None:
        """Real ModelCompactor: LENGTH → model summary compression → retry → STOP."""
        adapter = _ScriptedAdapter([
            ModelResponse(content="", stop_reason=StopReason.LENGTH),
            ModelResponse(content="recovered", stop_reason=StopReason.STOP),
        ])
        loop = _make_loop(adapter, compactor=ModelCompactor())
        _seed(loop, n=8)  # enough to trigger real compaction
        before = len(loop._messages)
        result = loop.step()

        assert result.kind == "awaiting_input"
        assert len(loop._messages) < before
        types = [e.event_type for e in loop.recorder.events]
        assert EventType.CONTEXT_COMPACTION in types
