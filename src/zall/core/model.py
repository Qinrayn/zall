"""zall.core.model — ModelAdapter interface (PR-3 model-agnostic).

Corresponds to:
  PR-3    模型无关: core 不 import 任何模型 SDK
  §0      agent 不许幻觉: ModelResponse 显式区分 content vs tool_calls

本文件是**纯接口** (Protocol + 数据结构), no implementations。
各家 Adapter (GLM / Claude / Gemini / Local) 在 zall.adapters 子包实现,
各自把 zall 的 Message/ToolCall/ModelResponse 翻译成自家 SDK 格式。

IPR constraints:
  IPR-0: invariant tests at tests/test_model_invariants.py, includesCounterexample
  IPR-1: this file corresponds to DESIGN.md PR-3 + §0 PR-0
  IPR-3: pydantic / stdlib only, no model SDK  ← 这是本文件的核心constraints
  IPR-4: this file is a primitive, no main Loop
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, model_validator


# ──────────────────────────────────────────────────────────────────────────
# StopReason (three-state, 与 §3.2.2 TerminationState 哲学一致)
# ──────────────────────────────────────────────────────────────────────────


class StopReason(str, Enum):
    """modelstop原因 (PR-3 model-agnostic)。

    统一 3 种, 不多不少 (与 §3.2.2 three-state哲学一致):
      STOP      — 模型说完了 (自然语言回复结束)
      TOOL_USE  — 模型要调工具 (结构化 tool_calls)
      LENGTH    — 上下文超限 (需 ContextManager 压缩)

    各家 Adapter 翻译:
      OpenAI "stop"          → STOP
      OpenAI "tool_calls"    → TOOL_USE
      OpenAI "length"        → LENGTH
      Anthropic "end_turn"   → STOP
      Anthropic "tool_use"   → TOOL_USE
      Anthropic "max_tokens" → LENGTH

    Counterexample: 如果有人加第 4 种 (eg. "content_filter"), 须先在 DESIGN.md 立 OPEN。
    """

    STOP = "stop"
    TOOL_USE = "tool_use"
    LENGTH = "length"


# ──────────────────────────────────────────────────────────────────────────
# ToolChoice (model调用时的tool选择strategy)
# ──────────────────────────────────────────────────────────────────────────


class ToolChoice(str, Enum):
    """tool选择strategy (PR-3 model-agnostic)。

    3 种:
      AUTO     — 模型自己决定调不调工具
      REQUIRED — 模型必须调工具 (不许纯文本回复)
      NONE     — 模型不许调工具 (纯文本回复)

    各家 Adapter 翻译成自家 SDK 的 tool_choice 值。
    """

    AUTO = "auto"
    REQUIRED = "required"
    NONE = "none"


# ──────────────────────────────────────────────────────────────────────────
# ToolCall (model产出的结构化tool调用)
# ──────────────────────────────────────────────────────────────────────────


class ToolCall(BaseModel):
    """model产出的单个tool调用 (PR-3 model-agnostic)。

    IPR-0 不变量:
        - frozen
        - id 非空 (模型给的调用 ID, 用于结果回灌)
        - tool_id 非空 (对应 ToolRegistry 中的 tool_id)
        - args 是 dict (与 Action.args 同型, 已知 OPEN: dict 可变)

    PR-0 落地:
        如果模型"幻觉式调工具" (在 content 里编 grep 输出, 不产 ToolCall),
        Agent Loop 能通过 stop_reason=STOP + tool_calls=[] 判定模型没真调工具。
    """

    model_config = ConfigDict(frozen=True)

    id: str
    tool_id: str
    args: dict[str, Any] = {}


# ──────────────────────────────────────────────────────────────────────────
# Message (zall 自己的message结构, 不绑死任何 SDK)
# ──────────────────────────────────────────────────────────────────────────


class Message(BaseModel):
    """对话message (PR-3 model-agnostic)。

    role:
        "user"      — 用户消息
        "assistant" — 模型回复
        "tool"      — 工具结果回灌

    content:
        自然语言文本。tool 角色时是工具的文本化输出。

    tool_call_id:
        only role="tool" 时有值, 对应被回灌的 ToolCall.id。

    tool_calls:
        only role="assistant" 且模型要调工具时有值。

    IPR-0 不变量:
        - frozen
        - role="tool" 时 tool_call_id 必须非空 (回灌须指明对应哪个 tool_call)

    Counterexample: role="tool" 但 tool_call_id=None → 须 raise (回灌歧义)。
    """

    model_config = ConfigDict(frozen=True)

    role: str
    content: str = ""
    tool_call_id: str | None = None
    tool_id: str = ""  # tool 角色时的工具名 (如 "read_file"), Gemini function_response 用
    tool_calls: tuple[ToolCall, ...] = ()

    @model_validator(mode="after")
    def _tool_role_requires_call_id(self) -> "Message":
        """role="tool" 时 tool_call_id must非空 (回灌须指明corresponds to哪个 tool_call)。

        Counterexample: role="tool" 但 tool_call_id=None → 须 raise (回灌歧义)。
        """
        if self.role == "tool" and not self.tool_call_id:
            raise ValueError(
                "role='tool' 时 tool_call_id 必须非空 "
                "(回灌须指明对应哪个 ToolCall, 否则 Agent Loop 不知道结果塞给谁)"
            )
        return self

    @classmethod
    def user(cls, content: str) -> Message:
        """construct user message。"""
        return cls(role="user", content=content)

    @classmethod
    def assistant(
        cls,
        content: str = "",
        tool_calls: tuple[ToolCall, ...] = (),
    ) -> Message:
        """construct assistant message。"""
        return cls(role="assistant", content=content, tool_calls=tool_calls)

    @classmethod
    def tool_result(cls, tool_call_id: str, content: str, tool_id: str = "") -> Message:
        """construct tool 结果回灌message。"""
        return cls(role="tool", content=content, tool_call_id=tool_call_id, tool_id=tool_id)


# ──────────────────────────────────────────────────────────────────────────
# ModelResponse (model调用的统一return)
# ──────────────────────────────────────────────────────────────────────────


class ModelResponse(BaseModel):
    """model调用的统一return (PR-3 model-agnostic)。

    IPR-0 不变量:
        - frozen
        - stop_reason 与 tool_calls 的consistency:
          stop_reason=TOOL_USE → tool_calls 非空
          stop_reason=STOP     → tool_calls 可空 (纯文本回复)
          stop_reason=LENGTH   → tool_calls 可空 (上下文超限)

    PR-0 落地:
        显式结构化让 Agent Loop 能判"模型真调了工具" vs "模型幻觉编了输出"。
        如果 stop_reason=STOP 但 content 里有 "grep 结果: ...",
        Loop 知道这是幻觉, 不是真工具调用。
    """

    model_config = ConfigDict(frozen=True)

    content: str = ""
    # model思考过程 (extended thinking / reasoning): DeepSeek-R1 / Qwen3-thinking /
    # GLM 等 OpenAI-compatible model在 delta.reasoning_content 里stream式给出。
    # zall 把它当作透明的"思考过程投影" (§9.2.12), 不进 PR-0 hallucinationjudgment
    # (PR-0 只扫 content 里的false造tooloutput, reasoning 不在 content 中)。
    reasoning: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    stop_reason: StopReason
    raw: dict[str, Any] = {}  # 原始 SDK 响应 (调试用, 不参与逻辑)
    usage: dict[str, int] = {}  # token 统计 (eg. {"prompt": 100, "completion": 50})


# ──────────────────────────────────────────────────────────────────────────
# ModelAdapter Protocol (PR-3 核心interface)
# ──────────────────────────────────────────────────────────────────────────


@runtime_checkable
class ModelAdapter(Protocol):
    """modeladapterprotocol (PR-3 model-agnostic核心interface)。

    core 层只依赖此 Protocol, 不依赖任何具体 SDK。
    各家 Adapter (GLM / Claude / Gemini / Local) 在 zall.adapters 实现。

    constraints (IPR-3):
        - 本 Protocol 定义在 core/ 下, core/ 不 import 任何 SDK
        - 各 Adapter 在 adapters/ 下 import SDK, 翻译成 zall 格式

    非streaming: 本轮只落非streaming接口; streaming deferred (不假装)。

    可选 streaming (P2):
        实现方可选提供 complete_stream 方法, 返回 Iterator[tuple[str, ModelResponse]]。
        AgentLoop 用 hasattr(adapter, 'complete_stream') 检测, 不强制。
        流式语义 ≡ 阻塞: 最终产出的 ModelResponse 必须与 complete() 等价
        (content + tool_calls + stop_reason 一致), 只是过程中逐 token 广播。
        不提供 complete_stream 的 adapter 自动降级到 complete()。
    """

    @property
    def model_name(self) -> str: ...

    def complete(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]],
        tool_choice: ToolChoice = ToolChoice.AUTO,
    ) -> ModelResponse: ...
