"""zall.core —— agent 本体论 4 维的代码投影 (DESIGN.md §1.2)。

本包为**模型无关**的纯接口聚合层 (Protocol / ABC / Pydantic):

  goal        .  §3 Goal 维度的代码投影
  authority   .  §4 Authority 维度的代码投影
  accountabil.. .  §5 Accountability 维度的代码投影
  verifiabil.. .  §6 Verifiability 维度的代码投影

  agent       .  §9.2 AgentDefinition + ToolsetPreset + SubagentCapabilityMode (v0.3.0)
  toolset     .  §4.2 工具集预设系统 (v0.3.0)

v0.4.4 新增模块:
  loop_errors  — ToolNotFound, AgentRunaway, ContextLimitExceeded
  loop_events  — LoopEvent, RunEgress, StepResult, MAX_STEPS
  loop_config  — AgentConfig, _GitProtectProtocol
  tool_kind    — ToolKind, ToolNamespace
  policies     — CompactionPolicy, ReminderPolicy

constraints (来自 IMPL.md):
  - IPR-3: 本包内**禁止** import 任何模型 SDK
  - IPR-4: 本包不写主 Loop; 主 Loop 在 zall.safety 之上的 orchestrator 中
  - IPR-0: 每个 primitive 必须 invariant test先于或同步落码
  - IPR-1: 每个 primitive 必须 DESIGN.md 节号对应
"""

from zall.core.extension import Extension, ExtensionRegistry  # noqa: F401

# v0.3.0: AgentDefinition system
from zall.core.agent import (  # noqa: F401
    AgentDefinition,
    AgentScope,
    PermissionMode,
    SubagentCapabilityMode,
    ToolsetPreset,
    discover_agents,
    filter_tools_by_capability,
    get_named_agent,
)

# v0.3.0: Toolset presets
from zall.core.toolset import (  # noqa: F401
    build_native_tools_for_preset,
    filter_tools_by_ids,
    get_tool_ids_for_preset,
    list_presets,
)

# v0.4.4: Loop 模块拆分
from zall.core.loop_errors import ToolNotFound, AgentRunaway, ContextLimitExceeded  # noqa: F401
from zall.core.loop_events import LoopEvent, RunEgress, StepResult, MAX_STEPS  # noqa: F401
from zall.core.loop_config import AgentConfig, _GitProtectProtocol  # noqa: F401
from zall.core.tool_kind import ToolKind, ToolNamespace  # noqa: F401
from zall.core.policies import CompactionPolicy, ReminderPolicy  # noqa: F401

__all__ = [
    # Extension system
    "Extension", "ExtensionRegistry",
    # Agent definition
    "AgentDefinition", "AgentScope", "PermissionMode",
    "SubagentCapabilityMode", "ToolsetPreset",
    "discover_agents", "filter_tools_by_capability", "get_named_agent",
    # Toolset presets
    "build_native_tools_for_preset", "filter_tools_by_ids",
    "get_tool_ids_for_preset", "list_presets",
    # Loop errors
    "ToolNotFound", "AgentRunaway", "ContextLimitExceeded",
    # Loop events
    "LoopEvent", "RunEgress", "StepResult", "MAX_STEPS",
    # Loop config
    "AgentConfig", "_GitProtectProtocol",
    # Tool taxonomy
    "ToolKind", "ToolNamespace",
    # Policies
    "CompactionPolicy", "ReminderPolicy",
]
