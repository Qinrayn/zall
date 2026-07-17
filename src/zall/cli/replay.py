"""zall.cli.replay — §6.2 session replay (复现, 不重新调model/tool)。

Corresponds to:
  §6.2 Replay: replays recorded responses (not re-calling model/tools/judge)
              compares reproduced RunEgress to original

核心原则 (§6.2):
  - replay 用 recorded model_call 的 content/tool_calls 构造 ModelResponse
  - replay 用 recorded tool_call_end 的 output 构造 ToolResult
  - 不调真模型 (ReplayAdapter.complete 不发 HTTP)
  - 不真执行工具 (ReplayTool.execute 不碰文件系统)
  - 复现结论, 不复现生成 (temp>0 的 token 生成是 development_aid, 不参与)

IPR constraints:
  IPR-0: invariant tests at tests/test_replay_invariants.py
  IPR-1: corresponds to DESIGN.md §6.2
  IPR-3: only stdlib, no model SDK
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from zall.core.accountability import JudgeVerdict
from zall.core.context import Context
from zall.core.goal import (
    AcceptanceContract, GoalStatement, GoalTriple, GoalType, TerminationState,
)
from zall.core.loop import AgentLoop
from zall.core.loop_config import AgentConfig
from zall.core.loop_events import RunEgress
from zall.core.model import (
    Message, ModelResponse, StopReason, ToolCall, ToolChoice,
)
from zall.core.safety import RuleSet, SafeLevel
from zall.core.tool import Tool, ToolRegistry, ToolResult
from zall.core.gate import UserResponse, UserResponseType
from zall.core.action import Action


# ──────────────────────────────────────────────────────────────────────────
# ReplayAdapter: 从 timeline 读 recorded model_call, return recorded ModelResponse
# ──────────────────────────────────────────────────────────────────────────


class ReplayAdapter:
    """replay 用的 fake adapter: return timeline 里 recorded 的 ModelResponse。

    §6.2: 不调真模型。complete() 从 recorded model_call 序列按顺序返回。
    __test__ = False 防 pytest 误收。
    """

    __test__ = False

    def __init__(self, recorded_calls: list[dict[str, Any]]) -> None:
        """recorded_calls: 从 timeline parse出的 model_call payload list。"""
        self._calls = list(recorded_calls)
        self._idx = 0
        self._http_called = False  # 反例测试用: 确认没发 HTTP

    @property
    def model_name(self) -> str:
        return self._calls[0].get("model", "replay") if self._calls else "replay"

    def complete(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]],
        tool_choice: ToolChoice = ToolChoice.AUTO,
    ) -> ModelResponse:
        """return下一条 recorded ModelResponse (不发 HTTP)。"""
        if self._idx >= len(self._calls):
            # recorded 用完 → STOP (防无限循环)
            return ModelResponse(content="(replay exhausted)", stop_reason=StopReason.STOP)
        call = self._calls[self._idx]
        self._idx += 1

        # 从 recorded payload 重建 ModelResponse
        stop_reason = StopReason(call.get("stop_reason", "stop"))
        content = call.get("content", "")
        raw_tcs = call.get("tool_calls", [])
        tool_calls = tuple(
            ToolCall(id=tc.get("id", ""), tool_id=tc.get("tool_id", ""), args=tc.get("args", {}))
            for tc in raw_tcs
        )
        return ModelResponse(
            content=content,
            tool_calls=tool_calls,
            stop_reason=stop_reason,
        )


# ──────────────────────────────────────────────────────────────────────────
# ReplayTool: 从 timeline 读 recorded tool_call_end, return recorded output
# ──────────────────────────────────────────────────────────────────────────


class ReplayTool:
    """replay 用的 fake tool: return timeline 里 recorded 的 ToolResult。

    §6.2: 不真执行工具。execute() 从 recorded tool_call_end 按 tool_id 匹配返回。
    """

    __test__ = False

    def __init__(self, tool_id: str, recorded_results: list[dict[str, Any]]) -> None:
        self._tool_id = tool_id
        self._results = list(recorded_results)
        self._idx = 0

    @property
    def tool_id(self) -> str:
        return self._tool_id

    @property
    def schema(self) -> dict[str, Any]:
        # replay 不需要真 schema (model不真调, 用 recorded)
        return {"type": "function", "function": {
            "name": self._tool_id, "description": f"replay {self._tool_id}",
            "parameters": {"type": "object", "properties": {}},
        }}

    def execute(self, args: dict[str, Any]) -> ToolResult:
        """return下一条 recorded ToolResult (不碰filesystem)。"""
        if self._idx >= len(self._results):
            return ToolResult(success=False, output="[replay: no more recorded results]")
        result = self._results[self._idx]
        self._idx += 1
        return ToolResult(
            success=result.get("success", True),
            output=result.get("output", ""),
            error=result.get("error"),
        )


# ──────────────────────────────────────────────────────────────────────────
# timeline parse
# ──────────────────────────────────────────────────────────────────────────


@dataclass
class ParsedTimeline:
    """从 timeline.jsonl parse出的 replay input。"""
    model_calls: list[dict[str, Any]]
    tool_results: dict[str, list[dict[str, Any]]]  # tool_id -> results
    original_meta: dict[str, Any]


def parse_timeline(session_dir: str | Path) -> ParsedTimeline | None:
    """从 session directoryparse timeline + meta。

    返回 None = session 不完整 (无 timeline 或 meta)。
    """
    p = Path(session_dir)
    timeline_path = p / "timeline.jsonl"
    meta_path = p / "meta.json"
    if not timeline_path.exists() or not meta_path.exists():
        return None

    model_calls: list[dict[str, Any]] = []
    tool_results: dict[str, list[dict[str, Any]]] = {}

    with open(timeline_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            et = ev.get("event_type", "")
            payload = ev.get("payload", {})
            if et == "model_call":
                model_calls.append(payload)
            elif et == "tool_call_end":
                tid = payload.get("tool_id", "unknown")
                tool_results.setdefault(tid, []).append(payload)

    with open(meta_path, encoding="utf-8") as f:
        meta = json.load(f)

    return ParsedTimeline(
        model_calls=model_calls,
        tool_results=tool_results,
        original_meta=meta,
    )


# ──────────────────────────────────────────────────────────────────────────
# replay 主function
# ──────────────────────────────────────────────────────────────────────────


class _AutoAcceptResponder:
    """replay 用的 responder: 自动 ACCEPT (replay 不交互)。"""
    __test__ = False

    def ask(self, action: Action, judgement: Any) -> UserResponse:
        if judgement.level == SafeLevel.BLACKLIST:
            return UserResponse(response_type=UserResponseType.REJECT)
        return UserResponse(response_type=UserResponseType.ACCEPT)


class _NoOpJudge:
    """replay 用的 judge: 不judgment (replay 复现结论, 不重新judgment)。

    返回 undecidable —— replay 的目的是复现 timeline, 不是重新判定 met。
    """
    __test__ = False

    @property
    def judge_type(self) -> str:
        return "system"

    def __call__(self, evidence: Any) -> JudgeVerdict:
        return JudgeVerdict(state=TerminationState.UNDECIDABLE, report="replay")


def replay_session(session_dir: str | Path) -> tuple[RunEgress, dict[str, Any]] | None:
    """replay 一个 session, return (replayed_egress, original_meta)。

    §6.2: 用 recorded responses 重放, 不调真模型/工具。
    返回 None = session 不完整。
    """
    parsed = parse_timeline(session_dir)
    if parsed is None:
        return None

    # construct ReplayAdapter
    adapter = ReplayAdapter(parsed.model_calls)

    # construct ReplayTool (每个 recorded tool_id 一个)
    tools_list: list[Tool] = []
    for tid, results in parsed.tool_results.items():
        tools_list.append(ReplayTool(tid, results))
    if not tools_list:
        # 无tool的 session: 给一个 placeholder (AgentLoop 需要 ToolRegistry)
        tools_list.append(ReplayTool("_noop", []))
    registry = ToolRegistry(tools=tuple(tools_list))

    # construct最小 GoalTriple + Context (replay 不真judgment, 用占位)
    class _T:
        exposed_dependency_set: tuple[str, ...] | None = None
        def __call__(self, state: object) -> TerminationState:
            return TerminationState.UNDECIDABLE

    goal = GoalTriple(
        statement=GoalStatement(
            intent="replay", rewriting="replay", rewrite_confidence=1.0,
            goal_type=GoalType.UNKNOWN, translation_of=("s",), added_intent=(),
        ),
        termination=_T(),
        acceptance=AcceptanceContract(baseline_frozen_at="replay"),
    )

    class _CwdStub:
        cwd_path: str = "/replay"
        git_branch: str | None = None
        git_remote: str | None = None

    context = Context(user_raw="replay", cwd_meta=_CwdStub())

    loop = AgentLoop(
        model=adapter,
        tools=registry,
        rules=RuleSet(),  # 空规则 → 全 greylist → auto accept
        goal=goal,
        context=context,
        user_responder=_AutoAcceptResponder(),
        config=AgentConfig(judge=_NoOpJudge(), stream=False),
    )

    egress = loop.run(system_prompt="replay")
    return egress, parsed.original_meta


def compare_egress(replayed: RunEgress, original_meta: dict[str, Any]) -> dict[str, Any]:
    """compare replay 产出与原 meta。

    返回对比结果 dict:
      reproduced: bool (final_state 一致)
      replayed_state: str
      original_state: str
      step_match: bool
      tool_match: bool
    """
    r_state = replayed.final_state.value
    o_state = original_meta.get("final_state", "?")
    # replay 用 NoOpJudge → 恒 undecidable
    # 所以 final_state 不一定匹配 (原可能 met, replay undecidable)
    # 真正要比的是 step_count / tool_calls 是否一致 (复现行为, 不是judgment)
    r_steps = replayed.step_count
    o_steps = original_meta.get("step_count", -1)
    r_tools = replayed.total_tool_calls
    o_tools = original_meta.get("tool_calls", -1)

    return {
        "reproduced": r_steps == o_steps and r_tools == o_tools,
        "replayed_state": r_state,
        "original_state": o_state,
        "step_match": r_steps == o_steps,
        "tool_match": r_tools == o_tools,
        "replayed_steps": r_steps,
        "original_steps": o_steps,
        "replayed_tools": r_tools,
        "original_tools": o_tools,
    }
