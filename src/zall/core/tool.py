"""zall.core.tool — Tool + ToolRegistry (tool layer interface).

Corresponds to:
  §4.2   8 个核心工具: read_file / write_file / edit_file / bash /
         grep / glob / list_dir / spawn_subagent
  §4.2   ToolRegistry: tool_id -> Tool 映射, 供 context_judge + confirm_gate 后执行

本文件only落**接口形态**, 不落任何具体工具实现 (守 IPR-2: 单 step only primitive + test)。
具体工具 (eg. bash, read_file) 在 zall.tools 子包后续轮次落码。

IPR constraints:
  IPR-0: invariant tests at tests/test_tool_invariants.py, includesCounterexample
  IPR-1: this file corresponds to DESIGN.md §4.2 (tool layer)
  IPR-3: pydantic / stdlib only, no model SDK
  IPR-4: this file is a primitive, no main Loop
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, model_validator


# ──────────────────────────────────────────────────────────────────────────
# ToolResult (统一returntype)
# ──────────────────────────────────────────────────────────────────────────


class ToolResult(BaseModel):
    """toolexecute的统一returntype。

    8 个工具各自把结果塞进 ToolResult:
      - success: 是否成功 (eg. bash exit code 0 = True)
      - output:  文本化输出 (给模型看的, eg. bash stdout / read_file 内容)
      - artifacts: 结构化产物 (给 Evidence 用, eg. test_results / diff)

    IPR-0 不变量:
        - frozen
        - output 非空 (即使失败也应有错误信息, 不允许静默失败)

    已知 OPEN:
        - artifacts: dict 可变 (与 Action.args / Evidence.external 同型, 不假装)
    """

    model_config = ConfigDict(frozen=True)

    success: bool
    output: str
    artifacts: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None  # 失败时的错误信息

    @staticmethod
    def __no_tool_history__() -> bool:
        """ToolResult 不携带 tool 调用历史 (§4.3 核心斩断呼应)。

        ToolResult 是"这一步的产出", 不是"agent 做过什么的日志"。
        tool 调用历史由 RunRecorder 记录, 不在 ToolResult 中。
        """
        return True


# ──────────────────────────────────────────────────────────────────────────
# Tool Protocol
# ──────────────────────────────────────────────────────────────────────────


@runtime_checkable
class Tool(Protocol):
    """toolprotocol (DESIGN.md §4.2 tool layer)。

    8 个核心工具各自实现此接口。接口统一, 但 execute 的内部逻辑不同:
      - read_file: 读文件, 返回line-numbered内容
      - bash: 执行命令, 返回 stdout/stderr
      - grep: ripgrep 封装, 返回匹配行
      - ...

    **execute 不是纯函数** (它改文件系统/跑 bash), 不测幂等性。
    invariant test只测: tool_id 非空 / schema 完整性。

    IPR-0 不变量:
        - tool_id 非空 (与 Action.tool_id 对应)
        - schema 是Valid JSON Schema dict (描述参数, 给模型看)
    """

    @property
    def tool_id(self) -> str: ...

    @property
    def schema(self) -> dict[str, Any]: ...

    def execute(self, args: dict[str, Any]) -> ToolResult: ...


# ──────────────────────────────────────────────────────────────────────────
# ToolRegistry (register中心, frozen)
# ──────────────────────────────────────────────────────────────────────────


class ToolRegistry(BaseModel):
    """toolregister中心 (DESIGN.md §4.2)。

    维护 tool_id -> Tool 映射。构造时传入 tuple (frozen, 与 RuleSet 同型)。
    MCP 工具后续加入时新构造一个 ToolRegistry (不可变 → 新实例)。

    IPR-0 不变量:
        - frozen
        - tool_ids 唯一 (不允许重复 tool_id, 否则查找歧义)

    Counterexample: 如果有人注册两个 tool_id="bash" 的工具, 须 raise。
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    tools: tuple[Tool, ...] = ()

    # B6 fix: instance级cache, 非class级 —— 不同 ToolRegistry instance不共享cache
    _schemas_cache_instance: list[dict[str, Any]] | None = None

    @model_validator(mode="after")
    def _tool_ids_must_be_unique(self) -> ToolRegistry:
        """tool_id 唯一性check。

        Counterexample: 重复 tool_id → 须 raise (查找歧义)。
        """
        seen: set[str] = set()
        for tool in self.tools:
            tid = tool.tool_id
            if tid in seen:
                raise ValueError(f"duplicate tool_id: {tid}")
            seen.add(tid)
        return self

    def get(self, tool_id: str) -> Tool | None:
        """按 tool_id find Tool。不存在return None。"""
        for tool in self.tools:
            if tool.tool_id == tool_id:
                return tool
        return None

    def has(self, tool_id: str) -> bool:
        """check tool_id 是否已register。"""
        return self.get(tool_id) is not None

    @property
    def tool_ids(self) -> tuple[str, ...]:
        """所有已register的 tool_id。"""
        return tuple(t.tool_id for t in self.tools)

    @property
    def schemas(self) -> list[dict[str, Any]]:
        """所有tool的 JSON Schema list (instance级cache, 惰性build)。

        B6 fix: 使用实例级缓存而非类级, 每个 ToolRegistry 实例独立缓存。
        """
        if self._schemas_cache_instance is None:
            import copy
            self._schemas_cache_instance = [copy.deepcopy(t.schema) for t in self.tools]
        return list(self._schemas_cache_instance)
