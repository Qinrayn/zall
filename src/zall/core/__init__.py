"""zall.core —— agent 本体论 6 维的代码投影 (MASTER.md §1.2)。

本包为**模型无关**的纯接口聚合层 (Protocol / ABC / Pydantic):

  6 维本体论 (MASTER.md §1.2):
    identity       .  §4.2.1 Identity: 身份 + 能力声明
    commitment     .  §4.2.2 Commitment: 目标 + 终止 + 验收
    perception     .  §4.2.3 Perception: 感知 + 世界模型 (v0.6.0)
    authority      .  §4.2.4 Authority: 权限 + 安全门
    accountability .  §4.2.5 Accountability: 判定 + 证据
    verifiability  .  §4.2.6 Verifiability: 审计 + 可复现

  跨维编排:
    loop           — AgentLoop 主循环编排器
    model          — ModelAdapter 协议 (PR-3 模型无关)
    tool           — ToolRegistry + Tool 协议
    ...

constraints:
  - IPR-3: 本包内**禁止** import 任何模型 SDK
  - IPR-4: 本包不写主 Loop; 主 Loop 在 zall.cli 之上的 orchestrator 中
  - IPR-0: 每个 primitive 必须 invariant test 先于或同步落码
  - IPR-1: 每个 primitive 必须 MASTER.md 节号对应
"""

# ──────────────────────────────────────────────────────────────────────────
# §4.2.1 Identity — 身份 + 能力声明
# ──────────────────────────────────────────────────────────────────────────
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

# ──────────────────────────────────────────────────────────────────────────
# §4.2.2 Commitment — 目标 + 终止 + 验收
# ──────────────────────────────────────────────────────────────────────────
from zall.core.goal import (  # noqa: F401
    GoalTriple,
    GoalStatement,
    GoalType,
    GoalDowngrade,
    TerminationCriterion,
    TerminationState,
    AcceptanceContract,
    RefinedGoal,
    DeclineTask,
    DowngradeGateState,
    Escalation,
)
from zall.core.refiner import GoalRefiner  # noqa: F401
from zall.core.plan_mode import PlanModeTracker, PlanModeState  # noqa: F401

# ──────────────────────────────────────────────────────────────────────────
# §4.2.3 Perception — 感知 + 世界模型 (v0.6.0)
# ──────────────────────────────────────────────────────────────────────────
from zall.core.perception import (  # noqa: F401
    Sensor,
    Observation,
    Percept,
    StateEstimate,
    WorldModel,
    PerceptionEngine,
)

# ──────────────────────────────────────────────────────────────────────────
# §4.2.4 Authority — 权限 + 安全门
# ──────────────────────────────────────────────────────────────────────────
from zall.core.safety import (  # noqa: F401
    RuleSet,
    SafeLevel,
    Judgement,
    Rule,
    context_judge,
)
from zall.core.gate import (  # noqa: F401
    ConfirmGate,
    GateState,
    GateResult,
    UserResponse,
    UserResponseType,
    OverrideEvent,
    EquivalenceRequest,
)
from zall.core.action import Action  # noqa: F401

# ──────────────────────────────────────────────────────────────────────────
# §4.2.5 Accountability — 判定 + 证据
# ──────────────────────────────────────────────────────────────────────────
from zall.core.accountability import (  # noqa: F401
    Judge,
    JudgeVerdict,
    Evidence,
    AccountabilityResult,
    CaveatType,
    TestCaseResult,
    LintResult,
    base_judge,
)

# ──────────────────────────────────────────────────────────────────────────
# §12.3 E3 Science Kit — 假设生命周期 + 证据管理
# ──────────────────────────────────────────────────────────────────────────
from zall.core.hypothesis import (  # noqa: F401
    Hypothesis,
    HypothesisStatus,
)

# ──────────────────────────────────────────────────────────────────────────
# §4.2.6 Verifiability — 审计 + 可复现
# ──────────────────────────────────────────────────────────────────────────
from zall.core.verifiability import (  # noqa: F401
    RunRecorder,
    TimelineEvent,
    EventType,
    TrustAnchor,
    FileTrustAnchor,
    AckEvent,
    TrustAnchorInit,
)

# ──────────────────────────────────────────────────────────────────────────
# 跨维模块 (不专属某一维度)
# ──────────────────────────────────────────────────────────────────────────
from zall.core.extension import Extension, ExtensionRegistry  # noqa: F401
from zall.core.toolset import (  # noqa: F401
    build_native_tools_for_preset,
    filter_tools_by_ids,
    get_tool_ids_for_preset,
    list_presets,
)
from zall.core.loop_errors import ToolNotFound, AgentRunaway  # noqa: F401
from zall.core.loop_events import LoopEvent, RunEgress, StepResult, MAX_STEPS  # noqa: F401
from zall.core.loop_config import AgentConfig, _GitProtectProtocol  # noqa: F401
from zall.core.tool_kind import ToolKind, ToolNamespace  # noqa: F401
from zall.core.policies import CompactionPolicy, ReminderPolicy  # noqa: F401

__all__ = [
    # ── Identity (§4.2.1) ──
    "AgentDefinition", "AgentScope", "PermissionMode",
    "SubagentCapabilityMode", "ToolsetPreset",
    "discover_agents", "filter_tools_by_capability", "get_named_agent",
    # ── Commitment (§4.2.2) ──
    "GoalTriple", "GoalStatement", "GoalType",
    "GoalDowngrade", "TerminationCriterion", "TerminationState",
    "AcceptanceContract", "RefinedGoal", "DeclineTask",
    "DowngradeGateState", "Escalation",
    "GoalRefiner", "PlanModeTracker", "PlanModeState",
    # ── Perception (§4.2.3, v0.6.0) ──
    "Sensor", "Observation", "Percept", "StateEstimate",
    "WorldModel", "PerceptionEngine",
    # ── Authority (§4.2.4) ──
    "RuleSet", "SafeLevel", "Judgement", "Rule",
    "context_judge", "ConfirmGate", "GateState", "GateResult",
    "UserResponse", "UserResponseType", "OverrideEvent", "EquivalenceRequest",
    "Action",
    # ── Accountability (§4.2.5) ──
    "Judge", "JudgeVerdict", "Evidence", "AccountabilityResult",
    "CaveatType", "TestCaseResult", "LintResult", "base_judge",
    # ── E3 Science Kit (§12.3) ──
    "Hypothesis", "HypothesisStatus",
    # ── Verifiability (§4.2.6) ──
    "RunRecorder", "TimelineEvent", "EventType",
    "TrustAnchor", "FileTrustAnchor", "AckEvent", "TrustAnchorInit",
    # ── Cross-cutting ──
    "Extension", "ExtensionRegistry",
    "build_native_tools_for_preset", "filter_tools_by_ids",
    "get_tool_ids_for_preset", "list_presets",
    "ToolNotFound", "AgentRunaway",
    "LoopEvent", "RunEgress", "StepResult", "MAX_STEPS",
    "AgentConfig", "_GitProtectProtocol",
    "ToolKind", "ToolNamespace",
    "CompactionPolicy", "ReminderPolicy",
]