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

from dataclasses import dataclass
from enum import Enum
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, model_validator

from zall.core.tool_kind import ToolKind, ToolNamespace

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
# ToolStreamItem — 工具流式协议 (v0.5.0, Grok Build 启发)
# ──────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ToolStreamItem:
    """工具执行的流式产出单元。

    参考 Grok Build 的 ToolStreamItem 协议:
      - Progress: 中间进度通知 (文本、内容块、自定义)
      - Terminal: 最终结果 (ToolResult)

    工具可以 yield 多个 Progress item 后跟一个 Terminal item。
    只执行 blocking (run) 的工具自动包装为单 Terminal stream。
    """

    type: Literal["progress", "terminal"]
    text: str = ""
    content_type: str = ""  # "text", "code", "diff", "status"
    result: ToolResult | None = None


# ──────────────────────────────────────────────────────────────────────────
# ToolCapabilities — 每个工具声明的能力 (v0.5.1, Grok Build 启发)
# ──────────────────────────────────────────────────────────────────────────


class ToolScope(str, Enum):
    """工具作用域: 读操作 vs 写操作。

    Read:  只读, 不修改文件系统/环境/外部状态
    Write: 可修改文件系统/环境/外部状态

    用于权限决策链 (context_judge) 和 plan mode 强制执行。
    """
    Read = "read"
    Write = "write"


@dataclass(frozen=True)
class ToolCapabilities:
    """工具的能力声明。

    每个工具通过 capabilities 属性声明自己的行为特征。
    默认值 (Write, 非只读) 是最安全的假设:
      工具默认是可写的, 除非显式声明只读。

    Attributes:
        is_read_only: 是否只读 (不修改任何外部状态)
        tool_scope:   作用域 (Read/Write)

    Reference:
        Grok Build's xai-tool-protocol/src/capabilities.rs
        ToolCapabilities { is_read_only: bool, tool_scope: ToolScope }
    """
    is_read_only: bool = False
    tool_scope: ToolScope = ToolScope.Write


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

    可选属性 (Phase 2: ToolKind 分类):
        - kind: ToolKind (默认 OTHER)
        - namespace: ToolNamespace (默认 ZALL)
        - tool_version: str (默认 "0.0.0", §12 Plugin 版本化支柱)
        如果没有定义, 视为 ToolKind.OTHER / ToolNamespace.ZALL / version "0.0.0"。
    """

    @property
    def tool_id(self) -> str: ...

    @property
    def schema(self) -> dict[str, Any]: ...

    def execute(self, args: dict[str, Any]) -> ToolResult: ...

    # v0.5.1: 可选的能力声明 — 若无则默认 Write/非只读
    # @property
    # def capabilities(self) -> ToolCapabilities: ...


# ──────────────────────────────────────────────────────────────────────────
# validate_tool_args — 轻量 JSON Schema 子集校验器
# ──────────────────────────────────────────────────────────────────────────


def validate_tool_args(schema: dict, args: dict) -> list[str]:
    """轻量 JSON Schema 子集校验器, 只覆盖工具 schema 95% 场景。

    使用 stdlib, 不依赖 jsonschema 库 (IPR-3: core/ 只用 stdlib + pydantic)。

    覆盖:
      - required: 必填字段缺失 -> 错误
      - properties.<key>.type: 类型不符 -> 错误
        (支持 string/number/integer/boolean/object/array/null)
      - properties.<key>.enum: 值不在枚举内 -> 错误

    不覆盖 (超出轻量校验范围, 注释说明):
      - $ref / allOf / anyOf / oneOf / not
      - pattern / format / minLength / maxLength
      - minimum / maximum / multipleOf
      - minItems / maxItems / uniqueItems
      - default / const / if/then/else

    Args:
        schema: JSON Schema dict (type: object, properties, required)
        args:   待校验的参数 dict

    Returns:
        错误消息列表 (空列表 = 通过)。不抛异常, 调用方决定如何处理。

    Corresponds to MASTER.md §12.1 Plugin: schema 强制校验。
    """
    errors: list[str] = []

    if not isinstance(schema, dict):
        # schema 畸形 — 不崩溃, 返回错误
        return ["schema is not a dict"]

    properties = schema.get("properties", {})
    if not isinstance(properties, dict):
        properties = {}

    # 1. required 校验
    required = schema.get("required", [])
    if not isinstance(required, list):
        required = []
    for field in required:
        if field not in args:
            errors.append(f"missing required field: '{field}'")

    # 2. type + enum 校验
    for key, value in args.items():
        prop_schema = properties.get(key)
        if prop_schema is None or not isinstance(prop_schema, dict):
            continue

        # 2a. enum 校验
        enum_vals = prop_schema.get("enum")
        if enum_vals is not None and isinstance(enum_vals, list):
            if value not in enum_vals:
                errors.append(
                    f"field '{key}': value {value!r} not in enum {enum_vals}"
                )

        # 2b. type 校验
        expected_type = prop_schema.get("type")
        if expected_type is not None and isinstance(expected_type, str):
            if not _check_type(value, expected_type):
                errors.append(
                    f"field '{key}': expected type '{expected_type}', "
                    f"got {type(value).__name__}"
                )

    return errors


def _check_type(value: Any, expected_type: str) -> bool:
    """Check if a value matches the expected JSON Schema type.

    JSON Schema type mapping:
      string  -> str
      number  -> int | float (not bool, because bool is subclass of int)
      integer -> int (not bool)
      boolean -> bool
      object  -> dict
      array   -> list | tuple
      null    -> None
    """
    if expected_type == "string":
        return isinstance(value, str)
    elif expected_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    elif expected_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    elif expected_type == "boolean":
        return isinstance(value, bool)
    elif expected_type == "object":
        return isinstance(value, dict)
    elif expected_type == "array":
        return isinstance(value, (list, tuple))
    elif expected_type == "null":
        return value is None
    # Unknown type — pass through (lenient)
    return True


def assert_capabilities_declared(tool: Any) -> bool:
    """检查工具是否显式声明了 capabilities。

    内置工具豁免 (namespace == ZALL 时返回 True)。
    插件工具若未声明 capabilities, 返回 False。

    Corresponds to MASTER.md §12.1 Plugin: 沙箱 (能力声明强制化)。
    """
    from zall.core.tool_kind import ToolNamespace
    ns = get_tool_namespace(tool)
    if ns == ToolNamespace.ZALL:
        return True  # 内置工具豁免
    return hasattr(tool, 'capabilities') and tool.capabilities is not None


def get_tool_capabilities(tool: Any) -> ToolCapabilities:
    """获取工具的能力声明, 未声明时返回默认值 (Write/非只读)。"""
    if hasattr(tool, 'capabilities') and tool.capabilities is not None:
        return tool.capabilities
    return ToolCapabilities()  # 默认 Write, 非只读


def get_tool_kind(tool: Any) -> ToolKind:
    """Get a tool's kind, defaulting to ToolKind.OTHER if not set."""
    if hasattr(tool, 'kind') and tool.kind is not None:
        return tool.kind
    return ToolKind.OTHER


def get_tool_namespace(tool: Any) -> ToolNamespace:
    """Get a tool's namespace, defaulting to ToolNamespace.ZALL if not set."""
    if hasattr(tool, 'namespace') and tool.namespace is not None:
        return tool.namespace
    return ToolNamespace.ZALL


def get_tool_version(tool: Any) -> str:
    """Get a tool's version string, defaulting to "0.0.0" if not set.

    Corresponds to MASTER.md §12 Plugin 版本化支柱。
    版本用于第三方插件的兼容性检查 (SemVer)。内置工具默认 "0.0.0"。
    """
    if hasattr(tool, 'tool_version') and tool.tool_version:
        return str(tool.tool_version)
    return "0.0.0"


def parse_semver(version: str) -> tuple[int, int, int]:
    """Parse a SemVer string "X.Y.Z" into a tuple. Malformed -> (0,0,0).

    用于版本比较 (兼容性检查)。非严格 SemVer: 只取前三段数字。
    """
    parts = version.split(".")
    nums: list[int] = []
    for p in parts[:3]:
        try:
            # strip any pre-release suffix like "1.0.0-rc1"
            nums.append(int(p.split("-")[0].split("+")[0]))
        except (ValueError, IndexError):
            nums.append(0)
    while len(nums) < 3:
        nums.append(0)  # pad missing segments with 0 — "1.2" -> (1,2,0)
    return (nums[0], nums[1], nums[2])


def is_version_compatible(
    tool_version: str, required_min: str
) -> bool:
    """Check if tool_version >= required_min (SemVer major must match).

    Corresponds to MASTER.md §12 Plugin 版本化支柱。
    major 不同 = 不兼容 (breaking change)。minor/patch 向后兼容。
    """
    tv = parse_semver(tool_version)
    rv = parse_semver(required_min)
    if tv[0] != rv[0]:
        return False  # major 不匹配 = breaking
    return tv >= rv


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
    def tool_versions(self) -> dict[str, str]:
        """所有 tool 的版本 {tool_id: version} (§12 Plugin 版本化支柱)."""
        return {t.tool_id: get_tool_version(t) for t in self.tools}

    def check_compatibility(
        self, requirements: dict[str, str]
    ) -> dict[str, bool]:
        """检查注册表中的工具是否满足版本要求。

        Corresponds to MASTER.md §12 Plugin 版本化支柱。
        Args:
            requirements: {tool_id: required_min_version}
        Returns:
            {tool_id: is_compatible} (未注册的工具不在结果中)
        """
        result: dict[str, bool] = {}
        for tid, req_min in requirements.items():
            tool = self.get(tid)
            if tool is None:
                continue  # 未注册, 不报告
            result[tid] = is_version_compatible(get_tool_version(tool), req_min)
        return result

    @property
    def schemas(self) -> list[dict[str, Any]]:
        """所有tool的 JSON Schema list (实例级cache, 惰性build)。

        v0.5.1: 返回 cache 引用而非 deepcopy。ToolRegistry 是 frozen 的,
        tool schema 也是只读的, 调用方不应修改。如果调用方需要修改,
        应自行 copy。
        """
        if self._schemas_cache_instance is None:
            self._schemas_cache_instance = [t.schema for t in self.tools]
        return self._schemas_cache_instance
