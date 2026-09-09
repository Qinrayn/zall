"""zall.core.loop — Agent Loop orchestrator.

Corresponds to:
  §0      PR-0: no hallucination (stop_reason=STOP but content contains grep output -> hallucination)
  §3.2.2  TerminationCriterion three-state determination
  §4.2.1  context_judge safety evaluation
  §4.5    confirm_gate state machine
  §6.1    RunRecorder full recording + observer presentation projection (same record point, not a new primitive)

This module imports its building blocks from sibling modules:
  loop_config  → AgentConfig, _GitProtectProtocol
  loop_events  → MAX_STEPS, LoopEvent, RunEgress, StepResult
  loop_errors  → AgentRunaway

IPR constraints:
  IPR-0: invariant tests at tests/test_loop_invariants.py + tests/test_loop_observer_invariants.py
  IPR-1: this file corresponds to DESIGN.md §0 + §3.2.2 + §4.2.1 + §4.5 + §6.1
  IPR-3: pydantic / stdlib only, no model SDK (ModelAdapter is Protocol)
  IPR-4: this file IS the main Loop — IPR-4 unblock point
"""

from __future__ import annotations

import copy
import time
from collections.abc import Callable
from typing import Any
from uuid import uuid4

from zall._util.logging import get_zall_logger as _get_zall_logger
from zall.core import loop_checkpoint, loop_perception, loop_rewind
from zall.core.accountability import AccountabilityResult
from zall.core.action import Action
from zall.core.chat_state import ChatState
from zall.core.checkpoint import CheckpointManager
from zall.core.compactor import Compactor
from zall.core.context import Context
from zall.core.context_manager import ContextManager
from zall.core.events import EventBus
from zall.core.executor import ToolExecutor
from zall.core.extension import ExtensionRegistry
from zall.core.gate import (
    UserResponder,
    UserResponseType,
)
from zall.core.goal import GoalTriple, TerminationState

# ── Import from sibling modules (Phase 1 refactoring) ──
from zall.core.loop_config import AgentConfig, _GitProtectProtocol
from zall.core.loop_events import MAX_STEPS, LoopEvent, RunEgress, StepResult
from zall.core.model import (
    Message,
    ModelAdapter,
    ModelResponse,
    StopReason,
    ToolCall,
)
from zall.core.plan_mode import PlanModeState, PlanModeTracker
from zall.core.prompt_template import render as _render_template
from zall.core.refiner import GoalRefiner
from zall.core.safety import Judgement, RuleSet, SafeLevel
from zall.core.tool import ToolRegistry
from zall.core.verifiability import EventType, RunRecorder

_log = _get_zall_logger(__name__)


# ── Doom-loop detection constants (§3.6 Grok Build 启发) ──

# 环检测窗口: 记住最近 N 个 tool call 序列的哈希, 用于检测模型循环
_DOOM_LOOP_WINDOW_SIZE = 5
# 相同序列出现多少次触发警告
_DOOM_LOOP_WARN_THRESHOLD = 3
# 相同序列出现多少次触发终端终止
_DOOM_LOOP_TERMINAL_THRESHOLD = 5


def _tool_sequence_hash(tool_calls: tuple) -> str:
    """从 tool call 元组生成序列哈希 (仅 tool_id, 忽略参数)。"""
    return "|".join(tc.tool_id if hasattr(tc, 'tool_id') else str(tc)
                    for tc in (tool_calls or ()))


# 瞬态(可重试)错误分类 — 核心层真相源 (run/REPL/TUI 三路径共用)。
# 一次 429/5xx/timeout 不应杀死整个多步任务 (修"失败后中途就停")。IPR-3: 纯 stdlib。
TRANSIENT_KEYWORDS: tuple[str, ...] = (
    "429", "rate limit", "rate_limit", "timeout",
    "connection reset", "connection refused", "connection error",
    "503", "502",
    "temporary", "try again", "retry",
    "service unavailable", "bad gateway", "too many requests",
    "server error", "internal server error",
)


def is_transient_error(err: str | None) -> bool:
    """错误是否为瞬态/可重试 (429/5xx/timeout/connection 等)。

    Counterexample: 401/模型拒绝/空串/MAX_STEPS → False (不重试).
    """
    if not err:
        return False
    low = err.lower()
    # 明确排除非瞬态错误 (防止子串误匹配, 如 "MAX_STEPS=500" 中的 "500")
    if "max_steps" in low or "step limit" in low or "length exceeded" in low:
        return False
    return any(kw in low for kw in TRANSIENT_KEYWORDS)


# §10 I-0 六维本体论完整性 (Ontological Completeness, MASTER.md §1.2)
ONTOLOGY_DIMENSIONS: tuple[str, ...] = (
    "identity",           # ① 它是谁
    "commitment",         # ② 它承诺做什么
    "perception_engine",  # ③ 它如何理解世界
    "authority",          # ④ 它被允许用什么手段
    "accountability",     # ⑤ 做到什么程度算完成
    "verifiability",      # ⑥ 全过程可被第三方独立复核
)
"""六维本体论的运行时属性名 (MASTER.md §1.2)。I-7: 核心恰好暴露这 6 维。"""

# 强制维度: 缺失即"不是 agent, 是工具"(§1.2)。Perception/Accountability 可为 None
# (可选维度: Q&A 无 judge、无感知引擎仍是合法 agent, §12.1)。
_MANDATORY_DIMENSIONS: tuple[str, ...] = (
    "identity", "commitment", "authority", "verifiability",
)


def agent_has_all_dimensions(obj: Any) -> bool:
    """§10 I-0: 判断对象是否实现全部六维本体论组件。

    六维 = Identity/Commitment/Perception/Authority/Accountability/Verifiability。
    判据: (a) 六个维度属性槽位都存在; (b) 四个强制维度非 None。
    Perception/Accountability 允许 None (可选维度), 但属性必须存在。

    Counterexample: 缺任一维度属性, 或强制维度为 None → 返回 False
    (它退化为"工具"而非 agent, §1.2)。
    """
    for dim in ONTOLOGY_DIMENSIONS:
        if not hasattr(obj, dim):
            return False
    for dim in _MANDATORY_DIMENSIONS:
        if getattr(obj, dim, None) is None:
            return False
    return True


# ──────────────────────────────────────────────────────────────────────────
# AgentLoop (synchronous main controller)
# ──────────────────────────────────────────────────────────────────────────


class AgentLoop:
    """Agent Loop main controller (synchronous version).

    Orchestrates all primitives:
      ModelAdapter -> context_judge -> ConfirmGate -> ToolRegistry -> RunRecorder -> Judge

    Usage:
        loop = AgentLoop(
            model=adapter,
            tools=registry,
            rules=rule_set,
            goal=goal_triple,
            context=context,
            user_responder=responder,
            config=AgentConfig(judge=judge),
        )
        egress = loop.run()

    O9: 可选参数统一通过 `config: AgentConfig` 传入。
    旧式离散参数 (judge, observer, stream, ...) 保留签名供向后兼容,
    但建议新代码使用 AgentConfig。

    Stopping conditions:
      stop_reason=STOP -> check Goal termination -> return RunEgress
      stop_reason=LENGTH -> if compactor injected: auto-compact and retry;
                            still LENGTH / no compactor -> UNDECIDABLE termination (§9.2.9)
      step_count > MAX_STEPS -> raise AgentRunaway
    """

    def __init__(
        self,
        model: ModelAdapter,
        tools: ToolRegistry,
        rules: RuleSet,
        goal: GoalTriple,
        context: Context,
        user_responder: UserResponder,
        config: AgentConfig | None = None,
    ) -> None:
        # O9: 统一归一化为 AgentConfig
        _config = config if config is not None else AgentConfig()

        # stream/allow_downgrade/plan_mode/strict 的最终默认值
        _stream = _config.stream if _config.stream is not None else False
        _allow_downgrade = _config.allow_downgrade if _config.allow_downgrade is not None else True
        _plan_mode = _config.plan_mode if _config.plan_mode is not None else False
        _strict = _config.strict if _config.strict is not None else False

        self._model = model
        self._tools = tools
        self._rules = rules
        self._goal = goal
        self._context = context
        self._user_responder = user_responder
        self._judge = _config.judge
        self._judges = _config.judges  # §12.1 多 Judge 编排
        # EventBus takes priority over observer (v0.1.2)
        self._event_bus = _config.event_bus or EventBus()
        self._observer = _config.observer
        if _config.observer is not None:
            _observer = _config.observer
            # Legacy observer adapter via EventBus (avoids circular import in events.py)
            def _legacy_adapter(kind: str, payload: dict[str, Any]) -> None:
                _observer(LoopEvent(kind=kind, step=payload.get("step", 0), payload=payload))
            self._event_bus.on("*", _legacy_adapter)
        self._max_steps = _config.max_steps if _config.max_steps is not None and _config.max_steps >= 0 else MAX_STEPS
        # stream: True and adapter supports complete_stream -> use streaming (same semantics, broadcasts tokens)
        self._stream = _stream and hasattr(model, "complete_stream")
        # 重试可见性 (2026-07-26): adapter 静默退避期间转发 retry 事件给 UI。
        # duck-typed: core 不 import adapters (IPR-3); 无 set_retry_callback 则跳过。
        if hasattr(model, "set_retry_callback"):
            def _on_adapter_retry(
                category: str, delay: float, attempt: int, max_attempts: int,
            ) -> None:
                self._emit(LoopEvent(kind="retry", step=self._step_count, payload={
                    "category": category,
                    "delay": round(delay, 1),
                    "attempt": attempt,
                    "max_attempts": max_attempts,
                }))
            try:
                model.set_retry_callback(_on_adapter_retry)
            except Exception:
                pass  # 老 adapter 签名不兼容时降级为静默重试 (原行为)
        # GitProtect safety net: injected by CLI layer, core does not import tools/
        self._git_protect = _config.git_protect
        # CheckpointManager: filesystem snapshot safety net
        self._checkpoint_mgr = _config.checkpoint_mgr
        # plan_mode (§9.2.5 read-only posture) — write tools force greylist requiring confirmation.
        self._plan_mode = _plan_mode
        # v0.5.1: PlanModeTracker 状态机 (替代纯 bool)
        self._planner = _config.planner if _config.planner is not None else (
            PlanModeTracker() if not _plan_mode else PlanModeTracker(PlanModeState.Active)
        )
        # strict mode (v0.5.1): when True, full confirm/downgrade gates; when False, auto-skip.
        self._strict = _strict
        # §9.2.9 reactive auto-compact strategy (optional injection) via ContextManager.
        self._compactor = _config.compactor
        self._context_mgr = ContextManager(self, self._compactor)
        self._anchor = _config.anchor

        # ── 六维本体论 ① Identity (MASTER.md §1.2 + §4.2.1) ──
        # Identity 是必需维度: 无身份无法归因/追责 (§1.2 缺①)。未显式提供时构造
        # 默认身份, 保证 I-0 六维完整性不变量成立 (test_ontology_invariants)。
        from zall.core.agent import AgentIdentity
        self._identity: Any = _config.identity or AgentIdentity.default()

        # v0.6.0: Perception Engine (MASTER.md §4.2.3)
        self._perception_engine = _config.perception_engine
        self._last_perception_state: Any | None = None
        """最近一次感知状态估计 (用于 anomaly 检测和状态对比, §12.3 E1)"""
        self._prev_perception_snapshot: dict[str, Any] | None = None
        """上一步感知快照, 用于检测关键状态变化 (§12.3 E1.2)"""

        # v1.1: 记录 run 启动时 git modified 文件数基线, 用于 anomaly 检测
        # Bug fix (2026-07-26): 必须先于 _init_baseline_modified 赋值 _project_root —
        # 旧顺序 (271 行调用 / 341 行才赋值) 使 cwd=self._project_root 报
        # AttributeError 被 except 吞掉, 基线恒为 0 — 脏仓库 (>50 存量改动)
        # 下每步误报 "anomaly detected"。
        self._project_root: str = context.cwd_meta.cwd_path if hasattr(context, 'cwd_meta') else "."
        self._baseline_modified_files: int = 0
        self._init_baseline_modified()

        # Extension registry (Pi-style lifecycle hooks)
        self._ext_registry: ExtensionRegistry | None = _config.ext_registry

        # ToolExecutor: extracted tool execution orchestrator (v0.3.0)
        self._tool_executor = ToolExecutor(self)

        # ── v0.4.0: ChatState 集成 — Actor 模式消息管理 ──
        # v0.5.1: ChatState 是消息的唯一来源。
        # _messages 保留为向后兼容属性, 始终通过 _sync_messages() 与 ChatState 同步。
        self._chat_state: ChatState = _config.chat_state or ChatState(messages=[])
        # C3 fix: 从 ChatState 同步初始化 _messages (修复旧 bug: 若 chat_state
        # 预填消息, 旧代码 _messages=[] 与 _chat_state 不同步). _messages 保留为
        # 向后兼容影子属性 (多处测试直接赋值 loop._messages, 故不用 property).
        self._messages: list[Message] = list(self._chat_state.messages)

        self._run_id = uuid4().hex
        self._recorder = RunRecorder(
            self._run_id,
            spill_dir=getattr(_config, "timeline_spill_dir", None),
        )
        # _messages 通过 messages property 访问 (见 messages())
        self._step_count = 0
        self._tool_call_count = 0
        self._model_call_count = 0
        self._gate_decision_count = 0
        # PARADIGM Step 0: 最近一次 model 响应的真实 usage (供水位真实 token 计数)
        self._last_usage: dict[str, int] = {}
        # max_steps 软处理: 到上限时压缩上下文 + 重置步数继续, 最多重试这么多次
        self._max_steps_retries = 0
        self._MAX_STEPS_RETRIES = 2  # 最多压缩 2 次, 防无限重试
        # v0.4.9 (A1): last streaming exception, if any. Lets callers/observers
        # know a stream degraded — instead of silently pretending success.
        self._last_stream_error: BaseException | None = None
        # B9: SUSPENDED 计数器已在 executor.py 中以局部变量正确实现,
        # loop 层不再维护此状态。
        # O3: cached tool schemas from ToolRegistry cache (avoids per-instance deepcopy).
        # Fallback to per-loop deepcopy when tools is a plain iterable (test fixtures).
        if hasattr(self._tools, "schemas"):
            self._tool_schemas: list[dict[str, Any]] = list(self._tools.schemas)
        else:
            self._tool_schemas = [
                copy.deepcopy(tool.schema) for tool in self._tools.tools
            ]
        # O3: running tool usage counters (avoid scanning timeline)
        self._tool_usage_counts: dict[str, int] = {}
        # O6: cached git SHA results (avoid repeated subprocess calls)
        self._cached_git_sha: dict[str, str | None] = {}
        # B1: instance-level tracked file cache (not class-level, prevents multi-instance sharing)
        self._cached_tracked_files: set[str] | None = None

        # §3.6 Doom-loop detection (v0.5.0, inspired by Grok Build)
        self._tool_seq_history: list[str] = []
        """Circular buffer of recent tool call sequence hashes"""
        self._doom_loop_count: int = 0
        """Number of times doom-loop was detected"""
        self._last_doom_loop_step: int = 0
        """Step of last doom-loop detection (for debounce)"""

        # context_rewind (kimi D-Mail 对标): 逐步锚点 = 落锚时的消息列表长度。
        # 仅当 context_rewind 工具已注册才落锚 (未注册时零成本)。
        self._rewind_anchors: list[int] = []
        self._rewind_tool_checked: bool = False
        self._rewind_tool: Any = None

        # 工具重复调用梯度惩罚 (kimi r1/r2/r3/stop 升级链对标)
        from zall.core.repeat_guard import RepeatGuard
        self._repeat_guard = RepeatGuard()

        # 按步动态注入 providers (kimi DynamicInjectionProvider 对标;
        # 历史推断节流, 压缩后自愈重注入 — 见 core/dynamic_inject.py)
        from zall.core.dynamic_inject import PlanModeReminderProvider
        self._injection_providers: list[Any] = [PlanModeReminderProvider()]

        # 通知中心 (kimi background→notification→inject 闭环对标;
        # 仅主 loop 由 CLI 层显式设置, 子代理 loop 不设 → root-only 消费)
        self._notification_center: Any = None
        # P2 fix: 本 loop 消费的通知 scope (run 级隔离); None = 收全部
        self._notification_scope: str | None = None

        # §3.4 GoalDowngrade tracking
        self._allow_downgrade = _allow_downgrade
        self._original_goal: GoalTriple | None = None
        """Original overly-broad Goal — retained after downgrade, never deleted (R4)"""
        self._candidate_goals: tuple[GoalTriple, ...] = ()
        """Downgrade candidates — user-facing substitutes"""
        self._downgrade_depth: int = 0
        """Current downgrade depth"""
        self._final_claim: str = ""

        # Capture git SHA at run start, used for Evidence comparison
        self._run_start_sha: str | None = None
        # B3: project root 已在 __init__ 早期赋值 (_init_baseline_modified 之前)
        # O4: watermark check step gating delegated to ContextManager
        self._watermark_check_counter: int = 0  # kept for backward compat during refactor
        # v0.4.8: use ContextManager for watermark + compaction logic
        self._wm: Any | None = None  # removed — now managed by _context_mgr

    def _emit(self, event: LoopEvent) -> None:
        """Broadcast event to observer and EventBus (§6.1 presentation projection).

        EventBus is the primary channel (v0.1.2): multiple listeners can subscribe independently.
        observer connects via EventBus `*` wildcard listener (backward compatibility).

        observer exceptions are swallowed (IPR-0 counterexample):
          Presentation layer faults (e.g., a single print error causing different agent outputs
          -> violates reproducibility).
        """
        try:
            # EventBus broadcast
            self._event_bus.emit(event.kind, {
                "step": event.step,
                **event.payload,
            })
        except (KeyboardInterrupt, SystemExit):
            # B5: fatal signals must propagate, must not be swallowed
            raise
        except Exception as _emit_err:
            # IPR-0: Presentation layer failures must not affect RunEgress semantics,
            # but they must be observable (silent pass -> violates falsifiability).
            _log.warning(
                "observer _emit failed (IPR-0 safe): %s", _emit_err,
            )

    @property
    def run_id(self) -> str:
        return self._run_id

    @property
    def goal(self) -> GoalTriple:
        """当前lock的 Goal (§9.2.1/§9.2.5 UX 投影只读接缝, 不改控制stream)。"""
        return self._goal

    def _get_goal_type_str(self) -> str:
        """Extract goal type string for lifecycle hook inputs."""
        try:
            return self._goal.statement.goal_type.value
        except Exception:
            return "unknown"

    @property
    def recorder(self) -> RunRecorder:
        return self._recorder

    @property
    def event_bus(self) -> EventBus:
        """EventBus instance (v0.1.2: 多 listener event通道)。"""
        return self._event_bus

    # CLI 层query进度用 (§6.1 呈现层投影的只读接缝, 不改控制stream)
    @property
    def step_count(self) -> int:
        return self._step_count

    @property
    def max_steps(self) -> int:
        return self._max_steps

    # v0.0.22: 公开property, 供 /compact /doctor 等 CLI command只读访问 (替代直接访问 _private property)
    @property
    def messages(self) -> list[Message]:
        """当前 model context (只读snapshot, 不可直接修改)。

        v0.5.1: ChatState 是唯一来源, 通过 _chat_state.messages 读取。
        """
        return self._chat_state.messages

    def _sync_messages(self) -> None:
        """v0.5.2: 将 ChatState 的消息同步回 _messages 向后兼容属性。
        
        ChatState 是消息的唯一来源。_messages 保留为向后兼容的影子属性，
        供外部代码通过 loop._messages 直接读取时使用。
        """
        self._messages = list(self._chat_state.messages)

    @property
    def chat_state(self) -> ChatState | None:
        """ChatState 实例 (v0.4.0)。v0.5.1: 总是非 None。"""
        return self._chat_state

    def get_chat_state(self) -> ChatState:
        """获取或惰性创建 ChatState 实例。

        v0.5.1: ChatState 始终存在, 此方法仅保留向后兼容。
        """
        return self._chat_state

    @property
    def model_adapter(self) -> ModelAdapter:
        """当前 model adapter (只读)。"""
        return self._model

    @property
    def tool_call_count(self) -> int:
        """当前累计tool调用次数。"""
        return self._tool_call_count

    @property
    def model_call_count(self) -> int:
        """当前累计model调用次数。"""
        return self._model_call_count

    @property
    def plan_mode(self) -> bool:
        """当前 plan_mode state (委托到 PlanModeTracker)。"""
        return self._planner.is_active

    @property
    def planner(self) -> PlanModeTracker:
        """PlanModeTracker 实例 (供 executor/CLI 使用)。"""
        return self._planner

    @property
    def compactor(self) -> Compactor | None:
        """当前 compactor (可能为 None)。"""
        return self._compactor

    @property
    def perception_engine(self) -> Any | None:
        """当前 Perception Engine (可能为 None, MASTER.md §4.2.3)。"""
        return self._perception_engine

    # ── 六维本体论只读投影 (MASTER.md §1.2 + §10 I-0) ──
    # Identity/Commitment/Perception/Authority/Accountability/Verifiability
    @property
    def identity(self) -> Any:
        """① Identity — agent 身份 (恒非 None, §4.2.1)。"""
        return self._identity

    @property
    def commitment(self) -> GoalTriple:
        """② Commitment — 当前 Goal 承诺 (§4.2.2)。"""
        return self._goal

    @property
    def authority(self) -> Any:
        """④ Authority — 权限规则集 (§4.2.4)。"""
        return self._rules

    @property
    def accountability(self) -> Any | None:
        """⑤ Accountability — Judge (可为 None: Q&A 场景无判定, §12.1)。"""
        return self._judges if self._judges is not None else self._judge

    @property
    def verifiability(self) -> RunRecorder:
        """⑥ Verifiability — RunRecorder 链式哈希审计 (恒非 None, §6.1)。"""
        return self._recorder

    def set_plan_mode(self, enabled: bool) -> None:
        """更新 plan_mode state (供 CLI /plan command使用)。"""
        if enabled and not self._planner.is_active:
            self._planner.activate()
        elif not enabled and self._planner.is_active:
            self._planner.deactivate()
        self._plan_mode = enabled  # 保持向后兼容

    def set_notification_center(self, center: Any, scope: str | None = None) -> None:
        """设置通知中心 (仅主 loop; 子代理不设 → root-only 消费语义)。

        scope: 本 run 的通知归属 (P2 fix) — 只消费同 scope/全局通知,
        上一个 run 遗留的子代理通知不会注入本 loop 的上下文。
        """
        self._notification_center = center
        self._notification_scope = scope

    def set_messages(self, messages: list[Message]) -> None:
        """replace model context messagelist (供 /compact/CLI command使用)。

        IPR-0: 替换后 timeline 保留 (不删除已有事件), 但调用方应确保
        在 timeline 上追加 CONTEXT_COMPACTION 事件以维持可复现性。
        O1: 标记 token 估算缓存为脏。

        v0.5.1: ChatState 是唯一来源, _messages 通过 _sync_messages() 同步。
        """
        self._chat_state.replace_messages(messages)
        self._sync_messages()
        self._mark_watermark_dirty()

    # v0.1.3: 公开 API 供 CLI 层使用 (替代直接访问私有property)
    def add_user_file_message(self, content: str) -> None:
        """injectfilecontentmessage (供 /add command使用, 不走完整 Goal lock)。

        与 add_user_message 的区别: 文件注入是辅助上下文, 非用户新意图。
        O1: 标记 token 估算缓存为脏。

        v0.4.8: 当 ChatState 启用时, 委托给 ChatState.push_user_message()。
        v0.4.9: 修复 ChatState 启用时 _messages 不同步的 Bug。
        v0.4.10: 使用统一路径 _append_message, 消除重复逻辑。
        """
        self._append_message(Message(role="user", content=content))
        self._mark_watermark_dirty()

    def remove_messages_by_predicate(self, predicate: Callable[[Message], bool]) -> int:
        """按谓词removemessage, returnremovemessage数 (供 /drop /undo 等command使用)。

        timeline 保留 (不删除已有事件), 但调用方应确保已在 timeline 上
        追加适当事件 (如 CONTEXT_COMPACTION) 以维持可复现性。
        O1: 标记 token 估算缓存为脏。

        v0.5.1: 委托给 ChatState.remove_by_predicate(), _sync_messages() 保持同步。
        """
        removed = self._chat_state.remove_by_predicate(predicate)
        self._sync_messages()
        if removed > 0:
            self._mark_watermark_dirty()
        return removed

    @property
    def git_protect(self) -> _GitProtectProtocol | None:
        """GitProtect security网instance (只读, 供 CLI command使用)。"""
        return self._git_protect

    @property
    def checkpoint_manager(self) -> CheckpointManager | None:
        """CheckpointManager filesnapshot管理器 (只读, 供 CLI command使用)。"""
        return self._checkpoint_mgr

    # v0.0.22: tool调用statistics快速访问
    @property
    def tool_usage_summary(self) -> dict[str, int]:
        """return按 tool_id statistics的调用次数digest (供 /cost 等command使用)。
        
        使用 O3 缓存的计数器, 避免扫描 timeline。
        """
        return dict(self._tool_usage_counts)

    def run(self, system_prompt: str = "") -> RunEgress:
        """execute Agent Loop, return RunEgress。

        同步: 阻塞直到终止或异常。
        内部循环调 step() 直到 terminal。

        v0.0.11: 在进入主循环前检查是否需要 GoalDowngrade (§3.4)。
        """
        # O6: clear cached git SHA at start of each run
        self._cached_git_sha.clear()
        # B9 fix: 每次 run() 重置 watermark 计数器 (delegated to ContextManager)
        self._watermark_check_counter = 0
        self._context_mgr.reset_check_counter()
        # max_steps 软处理: 每次 run 重置重试计数
        self._max_steps_retries = 0

        # ── §3.4 GoalDowngrade: 进入主循环前checkdowngrade
        self._init_downgrade()

        # v0.0.6 fix (H1): 捕获运行开始时的 git SHA, 用于 Evidence compare
        self._run_start_sha = self._resolve_git_sha("HEAD")

        # init化: system prompt + user_raw 作为首条 user message
        self._chat_state.reset()
        if system_prompt:
            self._append_message(Message(role="system", content=system_prompt))
        self._append_message(Message.user(self._context.user_raw))
        # C3 fix: 移除冗余 _sync_messages() -- 上面两个 _append_message 内部已同步。

        # Extension: on_agent_start (legacy) + on_turn_start (typed)
        if self._ext_registry is not None:
            from zall.core.lifecycle import TurnStartInput
            _ts_input = TurnStartInput(
                goal=self._goal,
                model_name=getattr(self._model, "model_name", ""),
                messages=self._chat_state.messages,
                tools=self._tools.tools if self._tools else (),
                step=0,
            )
            self._ext_registry.fire_all(
                "on_agent_start", "on_turn_start",
                typed_input=_ts_input,
                goal=self._goal,
                model=self._model,
                messages=self._chat_state.messages,
            )

        # ── Phase 1 (修裂缝): 记录 goal_statement + user_confirm 到 timeline
        # §9.2.1 + §6.1: 在第一条 tool_call_start 之前, 必须有 goal_statement 和
        # user_confirm 事件。这是 PR-0 自证伪 + §6.1 事件先于行动的落地。
        #
        # 不变量 (IPR-0): timeline 中第一条 tool_call_start 之前必须有
        # goal_statement + user_confirm。反例: run 跳过 Refiner/confirm 直接进循环 →
        # 测试 test_refiner_integrates_with_run 捕获。
        _now = int(time.time() * 1000)
        self._recorder.append(
            event_id=f"goal_statement_{self._run_id}",
            ts=_now,
            event_type=EventType.GOAL_STATEMENT,
            payload={
                "intent": self._goal.statement.intent,
                "rewriting": self._goal.statement.rewriting,
                "goal_type": self._goal.statement.goal_type.value,
                "rewrite_confidence": self._goal.statement.rewrite_confidence,
                "translation_of": list(self._goal.statement.translation_of),
                "added_intent": list(self._goal.statement.added_intent),
                "termination_exposed": (
                    tuple(self._goal.termination.exposed_dependency_set)
                    if self._goal.termination.exposed_dependency_set is not None
                    else None
                ),
                "baseline_frozen_at": self._goal.acceptance.baseline_frozen_at,
            },
        )
        self._emit(LoopEvent(
            kind="goal_statement",
            step=0,
            payload={
                "goal_type": self._goal.statement.goal_type.value,
                "intent": self._goal.statement.intent[:500],
            },
        ))

        # user_confirm 事件 (run() 被调用意味着 confirm_goal 已通过;
        # 若 strict 模式, 确认在 orchestrator.confirm_goal 中已完成)
        self._recorder.append(
            event_id=f"user_confirm_{self._run_id}",
            ts=_now + 1,  # 晚于 goal_statement 1ms, 保证时序
            event_type=EventType.USER_CONFIRM,
            payload={
                "confirmed": True,
                "mode": "auto" if not self._strict else "strict",
                "goal_type": self._goal.statement.goal_type.value,
            },
        )
        self._emit(LoopEvent(
            kind="user_confirm",
            step=0,
            payload={"confirmed": True, "goal_type": self._goal.statement.goal_type.value},
        ))

        while True:
            result = self.step()
            # 容错: 瞬态错误(429/5xx/timeout)退避重试, 与 REPL/TUI 对齐 (修"失败后中途就停")。
            # 用 retry_step() 不漂移 step_count; 成功恢复则继续任务。
            if (result.is_terminal and result.egress
                    and is_transient_error(result.egress.error)):
                result = self._run_retry_transient(result)
            if result.is_terminal:
                if result.egress is None:
                    raise RuntimeError("terminal StepResult must have non-None egress")
                # max_steps 软处理: 到上限不直接终止, 先尝试压缩上下文 + 重置步数继续。
                # 用户痛点: "步数有限制是不是不合理" -- 限制是安全阀(防 doom loop 烧钱),
                # 但到上限就 UNDECIDABLE 太粗暴。Claude Code 的做法是压缩后继续。
                # 策略: 到上限时调 compactor 压缩, 若压缩成功(消息数减少)则重置步数继续;
                # 若压缩无效果或已重试超过 _MAX_STEPS_RETRIES 次, 才真正终止。
                if (
                    result.egress.error
                    and "MAX_STEPS" in result.egress.error
                    and self._max_steps_retries < self._MAX_STEPS_RETRIES
                ):
                    compacted = self._try_compact_for_continuation()
                    if compacted:
                        # 压缩成功, 重置步数继续
                        self._max_steps_retries += 1
                        self._step_count = self._max_steps // 2  # 给一半预算继续
                        self._emit(LoopEvent(
                            kind="steps_extended",
                            step=self._step_count,
                            payload={
                                "retry": self._max_steps_retries,
                                "msgs": len(self._messages),
                            },
                        ))
                        continue  # 回到 while True 继续跑
                # P1 fix (dogfood 发现): max_steps runaway 时也要调 judge,
                # 否则 --judge system 模式下 agent 跑满 max_steps 时 judge 永远不执行。
                # 仅在 max_steps 场景 (error 含 "MAX_STEPS") 调, 异常 terminal 不调
                # (异常时 judge 判定无意义, 且可能产生意外 egress 覆盖)。
                if (
                    result.egress.error
                    and "MAX_STEPS" in result.egress.error
                    and (self._judge is not None or self._judges is not None)
                ):
                    judged_egress = self._check_termination()
                    # 保留原 max_steps error, judge 判定覆盖 final_state
                    result = StepResult(
                        kind=result.kind,
                        egress=judged_egress.model_copy(
                            update={
                                "error": result.egress.error,
                                "chain_warning": result.egress.chain_warning,
                            }
                        ),
                        content=result.content,
                        tools_used=result.tools_used,
                    )
                # M2: anchor run tail before returning
                if self._anchor is not None:
                    self._recorder.anchor_to(self._anchor, int(time.time() * 1000))
                # Extension: on_session_end (legacy) + on_turn_done (typed)
                if self._ext_registry is not None:
                    from zall.core.lifecycle import TurnDoneInput
                    _td_input = TurnDoneInput(
                        egress=result.egress,
                        step_count=self._step_count,
                        tool_counts=dict(self._tool_usage_counts),
                        tool_errors={},
                        goal_type=self._get_goal_type_str(),
                    )
                    self._ext_registry.fire_all(
                        "on_session_end", "on_turn_done",
                        typed_input=_td_input,
                        egress=result.egress,
                    )
                # v0.4.10 (B1): auto-apply high-confidence adjust_k suggestions
                if self._ext_registry is not None:
                    self._auto_apply_suggestions()
                # §12.1 Verifiability: 运行时链完整性自检 (MASTER.md §12.1 + §3.1.4)
                # 不阻止运行, 只标记。IPR-3: stdlib only.
                chain_warning = self._check_chain_integrity()
                if chain_warning:
                    result = StepResult(
                        kind=result.kind,
                        egress=result.egress.model_copy(
                            update={"chain_warning": chain_warning}
                        ),
                        content=result.content,
                        tools_used=result.tools_used,
                    )
                return result.egress
            if result.kind == "awaiting_input":
                # task mode: model STOP → check Goal termination -> return RunEgress
                # (dialog mode does not call run(), it calls step() and waits on awaiting_input)
                egress = self._check_termination()
                if self._ext_registry is not None:
                    from zall.core.lifecycle import TurnDoneInput
                    _td_input = TurnDoneInput(
                        egress=egress,
                        step_count=self._step_count,
                        tool_counts=dict(self._tool_usage_counts),
                        tool_errors={},
                        goal_type=self._get_goal_type_str(),
                    )
                    self._ext_registry.fire_all(
                        "on_session_end", "on_turn_done",
                        typed_input=_td_input,
                        egress=egress,
                    )
                    # v0.4.10 (B1): auto-apply high-confidence adjust_k suggestions
                    self._auto_apply_suggestions()
                # §12.1 Verifiability: 运行时链完整性自检 (MASTER.md §12.1 + §3.1.4)
                chain_warning = self._check_chain_integrity()
                if chain_warning:
                    egress = egress.model_copy(update={"chain_warning": chain_warning})
                return egress
            # tool_used → 继续循环

    def _run_retry_transient(self, result: StepResult) -> StepResult:
        """run() 一次性路径的瞬态错误退避重试 (最多 3 次, 指数抖动退避)。

        返回: 成功恢复则为非 terminal (调用方继续循环); 非瞬态/耗尽则 terminal。
        用 retry_step() 避免 step_count 漂移。与 TUI/REPL 同源 backoff_delay (G13)。
        """
        from zall._util.backoff import backoff_delay
        for attempt in range(1, 4):
            delay = round(backoff_delay(attempt), 1)  # G13: 指数+抖动, 防惊群
            self._emit(LoopEvent(
                kind="transient_retry",
                step=self._step_count,
                payload={
                    "attempt": attempt, "max": 3, "delay": delay,
                    "error": ((result.egress.error if result.egress else "") or "")[:120],
                },
            ))
            time.sleep(delay)
            try:
                result = self.retry_step()
            except (KeyboardInterrupt, SystemExit, GeneratorExit):
                raise
            except Exception as e:
                return StepResult(
                    kind="terminal",
                    egress=self._make_egress(TerminationState.UNDECIDABLE, error=str(e)),
                )
            if not result.is_terminal:
                return result  # 成功恢复
            if result.egress and not is_transient_error(result.egress.error):
                return result  # 非瞬态 terminal → 不再重试
        return result  # 重试耗尽

    def step(self) -> StepResult:
        """execute一轮 (调model + 可能调tool), 不自动terminate。

        对话模式用: 反复调 step(), STOP 时返回 awaiting_input 等用户下一句。
        run() 内部也调 step(), 但会在 terminal 时返回 RunEgress。

        返回 StepResult:
          tool_used      — 模型调了工具, 已执行, 继续
          awaiting_input — 模型 STOP, 等用户输入 (对话模式暂停点)
          terminal       — 异常/runaway/length, egress 非空
        """
        self._step_count += 1
        if self._step_count > self._max_steps:
            self._emit(LoopEvent(kind="runaway", step=self._step_count,
                                 payload={"error": "max steps exceeded"}))
            return StepResult(
                kind="terminal",
                egress=self._make_egress(
                    TerminationState.UNDECIDABLE,
                    error=f"exceeded MAX_STEPS={self._max_steps} without termination",
                ),
            )
        return self._run_step_body()

    def retry_step(self) -> StepResult:
        """Retry the current step without incrementing step_count.

        v0.4.9 (A2): CLI-level transient error retry (e.g. 429/503) calls
        retry_step() instead of step() so the step counter does not drift.
        All internal invariants (watermark monitor, empty-stop backoff, etc.)
        behave identically to step() — only the counter increment is skipped.
        """
        return self._run_step_body()

    def _drain_interjections_and_check_watermark(self) -> None:
        """处理 mid-turn interjections 并检查水位。

        v0.5.0: 在每次 model call 前 drain interjections,
        追加 system 消息并记录到 timeline。然后委托 ContextManager
        检查 watermark, 当 context 接近满时提前触发 compaction。
        """
        interjections = self._context_mgr.drain_interjections()
        if interjections:
            for interjection_text in interjections:
                self._append_message(Message(
                    role="system",
                    content=_render_template(
                        "mid_turn_interjection",
                        text=interjection_text,
                    ),
                ))
                self._recorder.append(
                    event_id=f"interjection_{self._step_count}",
                    ts=int(time.time() * 1000),
                    event_type=EventType.SYSTEM_INJECTION,
                    payload={"reason": "interjection", "text": interjection_text[:200]},
                )

        self._context_mgr.check_watermark_before_call(
            self._chat_state.messages, self._model.model_name, self._step_count,
            real_tokens=(self._last_usage or {}).get("prompt") or None,
        )

    def _call_model_with_retry(self) -> ModelResponse | None:
        """调 model + 空 STOP backoff + LENGTH auto-compact。

        流程:
          1. 首次调 model (emit_model_call=False, 防 nudge 双重显示)
          2. 空 STOP backoff: 检测到空回复 → inject nudge 重试一次
          3. LENGTH auto-compact: 模型返回 LENGTH → 压缩后重试一次
          4. 压缩后仍 LENGTH → 广播 length_exceeded 事件, 返回 None 表示 terminal

        Returns:
          ModelResponse — 最终有效的模型响应 (stop_reason 为 STOP 或 TOOL_USE)
          None — LENGTH terminal, 调用方应构造 terminal StepResult
        """
        resp = self._call_model(emit_model_call=False)
        self._model_call_count += 1

        # v0.0.21 空 STOP backoff: model空reply → inject nudge retry一次
        if self._context_mgr.is_empty_stop(resp):
            self._context_mgr.handle_empty_stop(
                self._chat_state.messages, self._model_call_count, self._step_count,
            )
            resp = self._call_model()  # emit_model_call=True: 渲染重试结果
            self._model_call_count += 1
        else:
            # 非 nudge: 补发首次 model_call 渲染event (停 spinner + 显示结果)
            self._emit_model_call_event(resp)

        # LENGTH auto-compact: 反应式压缩后重试一次
        if resp.stop_reason == StopReason.LENGTH:
            if self._auto_compact(reason="model_length"):
                resp = self._call_model()
                self._model_call_count += 1
            # 压缩后仍 LENGTH → 诚实 terminate
            if resp.stop_reason == StopReason.LENGTH:
                self._emit(LoopEvent(kind="length_exceeded", step=self._step_count,
                                     payload={"error": "context length"}))
                return None

        # PARADIGM Step 0: 记录真实 usage, 供下一次水位判定用真实 token (非字符估算)
        self._last_usage = dict(resp.usage or {})
        return resp

    def _handle_model_stop_response(self, resp: ModelResponse) -> StepResult:
        """STOP 分支处理: API error 检测 + 幻觉扫描 + 返回 awaiting_input。

        流程:
          1. 检测 false STOP (API error 伪装成 STOP, HTTP status >= 400)
          2. PR-0: 扫描 STOP reply 中是否包含伪造的工具输出
          3. 追加 assistant reply 到 messages
          4. 返回 awaiting_input (step() 不自判 termination, 由 run() 处理)
        """
        # P0 fix: 检测伪装成 STOP 的 API error
        raw = resp.raw if isinstance(resp.raw, dict) else {}
        api_status = raw.get("status", 0) if raw else 0
        if api_status >= 400:
            err_msg = resp.content or f"HTTP {api_status}"
            self._emit(LoopEvent(
                kind="error",
                step=self._step_count,
                payload={"error": err_msg, "api_status": api_status},
            ))
            return StepResult(
                kind="terminal",
                egress=self._make_egress(
                    TerminationState.UNDECIDABLE,
                    error=f"API error (HTTP {api_status}): {err_msg}",
                ),
            )
        # PR-0 自证伪: 扫描 STOP reply 是否伪造了 tool output
        hallucinations = self._scan_hallucinated_content(resp.content)
        if hallucinations:
            self._recorder.append(
                event_id=f"pr0_warn_{self._model_call_count}",
                ts=int(time.time() * 1000),
                event_type=EventType.PR0_HALLUCINATION,
                payload={
                    "step": self._step_count,
                    "hallucination_tags": list(hallucinations),
                    "content_preview": resp.content[:200],
                },
            )
            self._emit(LoopEvent(
                kind="pr0_warning",
                step=self._step_count,
                payload={
                    "tags": list(hallucinations),
                    "message": "模型 STOP 回复中检测到伪造的工具输出 — 违 PR-0 自证伪",
                },
            ))
        # 把 assistant reply 加入 messages
        self._append_message(Message.assistant(content=resp.content))
        return StepResult(kind="awaiting_input", content=resp.content)

    def _handle_tool_use(self, resp: ModelResponse) -> StepResult:
        """TOOL_USE 分支处理: 执行工具 + 记录消息 + doom-loop 检测。

        流程:
          1. 空 tool_calls 检测 (PR-0 hallucination)
          2. 执行工具调用
          3. 追加 assistant message (含 tool_calls)
          4. Doom-loop 检测: 检查近期 tool call 序列是否重复
          5. 返回 tool_used StepResult
        """
        if not resp.tool_calls:
            err = ("stop_reason=TOOL_USE but tool_calls is empty — "
                   "model hallucinated tool use (PR-0 violation)")
            self._emit(LoopEvent(kind="error", step=self._step_count,
                                 payload={"error": err}))
            return StepResult(
                kind="terminal",
                egress=self._make_egress(TerminationState.UNDECIDABLE, error=err),
            )

        # §12.3 E1.5: 感知预测 (可选, 降级为 no-op)
        if self._perception_engine is not None and self._last_perception_state is not None:
            try:
                _tc = resp.tool_calls[0]
                _action = Action(tool_id=_tc.tool_id, args=dict(_tc.args or {}))
                _predicted = self._perception_engine.predict(_action)
                self._recorder.append(
                    event_id=f"perception_predict_{self._step_count}",
                    ts=int(time.time() * 1000),
                    event_type=EventType.SYSTEM_INJECTION,
                    payload={
                        "reason": "perception_predict",
                        "tool_id": _tc.tool_id,
                        "predicted_confidence": _predicted.confidence,
                        "predicted_keys": list(_predicted.state.keys()),
                    },
                )
            except Exception as _e:
                _log.debug("perception predict degraded to no-op: %s", _e)

        self._append_message(
            Message.assistant(content=resp.content, tool_calls=resp.tool_calls)
        )
        self._execute_tool_calls(resp.tool_calls)
        # context_rewind: 模型本步请求了上下文回滚 → 施加后直接进入下一步
        # (doom-loop 历史已在 apply 内复位, 无需再检测本步序列)
        if loop_rewind.apply_pending_rewind(self):
            return StepResult(
                kind="tool_used",
                tools_used=tuple(tc.tool_id for tc in resp.tool_calls),
            )
        # repeat_guard 强制止损 (kimi force_stop_turn 对标): 同一调用连击达阈,
        # 提醒已局尽 → 优雅结束本回合 (awaiting_input, 控制权交回用户)。
        if self._repeat_guard.force_stop:
            self._repeat_guard.reset()
            self._emit(LoopEvent(
                kind="repeat_force_stop",
                step=self._step_count,
                payload={"message": "identical tool call repeated past hard "
                                     "limit; turn stopped to cut losses"},
            ))
            return StepResult(
                kind="awaiting_input",
                content="(turn stopped: identical tool call repeated past the "
                        "hard limit without progress)",
            )
        # v0.5.0: Doom-loop detection — 检查重复 tool call 序列
        seq_hash = _tool_sequence_hash(resp.tool_calls)
        self._tool_seq_history.append(seq_hash)
        if len(self._tool_seq_history) > _DOOM_LOOP_WINDOW_SIZE:
            self._tool_seq_history.pop(0)
        seq_count = self._tool_seq_history.count(seq_hash)
        if seq_count >= _DOOM_LOOP_TERMINAL_THRESHOLD and seq_hash:
            if self._step_count - self._last_doom_loop_step >= 3:
                self._last_doom_loop_step = self._step_count
                self._doom_loop_count += 1
                err = (
                    f"doom-loop detected: tool sequence '{seq_hash}' "
                    f"repeated {seq_count} times in last "
                    f"{_DOOM_LOOP_WINDOW_SIZE} steps. "
                    f"Model is stuck in a loop."
                )
                self._emit(LoopEvent(
                    kind="doom_loop",
                    step=self._step_count,
                    payload={
                        "error": err,
                        "seq_hash": seq_hash,
                        "seq_count": seq_count,
                        "window": _DOOM_LOOP_WINDOW_SIZE,
                    },
                ))
                loop_nudge = _render_template("doom_loop_nudge")
                self._append_message(Message(role="system", content=loop_nudge))
                self._recorder.append(
                    event_id=f"doom_loop_{self._step_count}",
                    ts=int(time.time() * 1000),
                    event_type=EventType.SYSTEM_INJECTION,
                    payload={"reason": "doom_loop", "nudge": loop_nudge},
                )
        elif seq_count >= _DOOM_LOOP_WARN_THRESHOLD and seq_hash:
            self._emit(LoopEvent(
                kind="doom_loop_warning",
                step=self._step_count,
                payload={
                    "seq_hash": seq_hash,
                    "seq_count": seq_count,
                    "window": _DOOM_LOOP_WINDOW_SIZE,
                },
            ))
        return StepResult(
            kind="tool_used",
            tools_used=tuple(tc.tool_id for tc in resp.tool_calls),
        )

    def _run_step_body(self) -> StepResult:
        """Core step execution body shared by step() and retry_step().

        编排子方法完成一次 step 的执行:
          0. _perceive — 感知引擎更新状态估计 (v0.6.0)
          1. _drain_interjections_and_check_watermark — 排空 interjections + 水位检查
          2. _call_model_with_retry — 调 model + backoff + LENGTH 处理
          3. 按 stop_reason 分发到 _handle_model_stop_response 或 _handle_tool_use

        v0.6.0 (UX): 每个子步骤开始时广播 step_progress 事件, 呈现层显示进度提示。
        """
        try:
            # 子步骤 0: 感知 (v0.6.0, MASTER.md §4.2.3) — 逻辑抽取到 core/loop_perception.py
            # (PARADIGM Step 0 热循环瘦身); 感知引擎为 None 时该函数直接返回, 不进热路径。
            loop_perception.run_perception(self)
            # 子步骤 0.5: context_rewind 落锚 (kimi D-Mail 对标;
            # 工具未注册时 no-op, 逻辑见 core/loop_rewind.py)
            loop_rewind.maybe_drop_anchor(self)
            # 子步骤 0.6: 按步动态注入 (plan 模式纪律周期性重申等;
            # 历史推断节流, 无 provider/不命中时零成本)
            from zall.core.dynamic_inject import run_injections
            run_injections(self)
            # 子步骤 0.7: 后台通知递送 (并行子代理完成等 → 注入上下文;
            # 未设通知中心时零成本 — 见 core/notifications.py)
            from zall.core.notifications import deliver_into_loop
            deliver_into_loop(self)
            # 子步骤 1: 准备上下文
            self._emit(LoopEvent(
                kind="step_progress",
                step=self._step_count,
                payload={"phase": "context", "message": "preparing context..."},
            ))
            self._drain_interjections_and_check_watermark()

            # 子步骤 2: 调用模型
            self._emit(LoopEvent(
                kind="step_progress",
                step=self._step_count,
                payload={"phase": "model", "message": "calling model..."},
            ))
            resp = self._call_model_with_retry()
            if resp is None:
                return StepResult(
                    kind="terminal",
                    egress=self._make_egress(
                        TerminationState.UNDECIDABLE,
                        error="model returned LENGTH; context compaction "
                              "could not reduce further",
                    ),
                )

            if resp.stop_reason == StopReason.STOP:
                return self._handle_model_stop_response(resp)

            if resp.stop_reason == StopReason.TOOL_USE:
                return self._handle_tool_use(resp)

            raise RuntimeError(f"unexpected stop_reason: {resp.stop_reason}")

        except Exception as e:
            # IPR-0: 呈现层故障不得改变 RunEgress, 但致命信号must传播
            # 不用 except BaseException — GeneratorExit / 其他 BaseException subclass
            # 被吞会导致资源leak (generator未正确关闭、thread无法取消)
            if isinstance(e, (KeyboardInterrupt, SystemExit, GeneratorExit)):
                raise
            self._emit(LoopEvent(kind="error", step=self._step_count,
                                 payload={"error": str(e), "type": type(e).__name__}))
            return StepResult(
                kind="terminal",
                egress=self._make_egress(TerminationState.UNDECIDABLE, error=str(e)),
            )

    def _append_message(self, msg: Message) -> None:
        """内部追加消息。

        v0.5.1: ChatState 是唯一来源, 所有消息追加通过 ChatState 完成。
        _messages 通过 _sync_messages() 保持同步。
        """
        role = msg.role
        if role == "user":
            self._chat_state.push_user_message(msg.content)
        elif role == "assistant":
            self._chat_state.push_assistant_response(
                msg.content, tool_calls=msg.tool_calls or ()
            )
        elif role == "tool":
            self._chat_state.push_tool_result(
                msg.tool_call_id or "", msg.content,
                tool_id=msg.tool_id or "",
            )
        elif role == "system":
            self._chat_state.push_system_message(msg.content)
        self._sync_messages()

    def append_message(self, msg: Message) -> None:
        """公开 API: 追加消息 (供 executor.py 等外部组件使用)。

        v0.4.8: 统一消息追加路径, 当 ChatState 启用时自动同步。
        """
        self._append_message(msg)

    def finalize(self) -> RunEgress:
        """Dialog mode end: construct undecidable RunEgress (no session save, no judge).

        Dialog mode does not judge met/not_met (dialog has no "completion" concept).
        """
        egress = self._make_egress(TerminationState.UNDECIDABLE)
        # Extension: on_session_end (legacy) + on_turn_done (typed)
        if self._ext_registry is not None:
            from zall.core.lifecycle import TurnDoneInput
            _td_input = TurnDoneInput(
                egress=egress,
                step_count=self._step_count,
                tool_counts=dict(self._tool_usage_counts),
                tool_errors={},
                goal_type=self._get_goal_type_str(),
            )
            self._ext_registry.fire_all(
                "on_session_end", "on_turn_done",
                typed_input=_td_input,
                egress=egress,
            )
            # v0.4.10 (B1): auto-apply high-confidence adjust_k suggestions
            self._auto_apply_suggestions()
        return egress

    def _auto_apply_suggestions(self) -> None:
        """Auto-apply high-confidence adjust_k suggestions from extensions.

        v0.4.10 (B1): Called after on_turn_done to apply trustworthy
        suggestions. Currently only auto-applies adjust_k (K-value changes)
        with confidence >= 0.5. Other suggestion kinds require manual
        approval via /suggest apply.
        """
        if self._ext_registry is None:
            return
        try:
            suggestions = self._ext_registry.collect_suggestions()
        except Exception:
            return
        if not suggestions:
            return
        for s in suggestions:
            if s.kind == "adjust_k" and s.confidence >= 0.5:
                # Try to find and apply through the extension (v0.5.0: use public API)
                for ext in self._ext_registry.iter_extensions():
                    if hasattr(ext, "apply_suggestion") and callable(ext.apply_suggestion):
                        try:
                            result = ext.apply_suggestion(s)
                            if result.get("applied"):
                                self._emit(LoopEvent(
                                    kind="self_adjust",
                                    step=self._step_count,
                                    payload={
                                        "kind": s.kind,
                                        "target": s.target,
                                        "value": s.value,
                                        "message": result.get("message", ""),
                                    },
                                ))
                        except Exception as _e:
                            _log.warning(
                                "auto_learn: apply_suggestion failed for extension %s: %s",
                                getattr(ext, 'name', 'unknown'), type(_e).__name__,
                            )

    def add_user_message(self, content: str) -> None:
        """Dialog mode: user input appended as new user message.

        Section 4.3: user explicitly re-injects context, audited.
        O1: marks token estimation cache dirty.

        v0.4.8: 当 ChatState 启用时, 委托给 ChatState.push_user_message()。
        v0.4.9: 修复 ChatState 启用时 _messages 不同步的 Bug。
        v0.4.10: 使用统一路径 _append_message, 消除重复逻辑。
        """
        self._append_message(Message.user(content))
        self._mark_watermark_dirty()
        # 新回合: 重复连击追踪复位 (kimi 每回合 _last_tool_calls=[] 对标)
        self._repeat_guard.reset()

        # Extension: on_user_input (legacy + typed)
        if self._ext_registry is not None:
            from zall.core.lifecycle import UserInputReceived
            _ui_input = UserInputReceived(
                content=content,
                step=self._step_count,
            )
            self._ext_registry.fire_all(
                "on_user_input", "on_user_input",
                typed_input=_ui_input,
                content=content,
            )

    # O1: 标记 watermark token 估算cache为脏 (v0.4.8: delegates to ContextManager)
    def _mark_watermark_dirty(self) -> None:
        """当 messages 变化时, 通知 ContextManager 的 watermark monitor cache失效。"""
        self._context_mgr.mark_dirty()

    # ── §9.2.9 auto-compact: context压缩 (v0.0.18, v0.4.8: delegates to ContextManager) ──

    def _auto_compact(self, *, reason: str) -> bool:
        """自动压缩 model context window, return是否真的压缩了 (§9.2.9).

        v0.4.8: Delegates entirely to ContextManager, eliminating duplicate
        compact logic between loop.py and context_manager.py.

        - 无 compactor 注入 → False (行为与旧版一致, 不改变既有测试)。
        - compactor 抛异常 → 吞掉并广播 error 事件, 返回 False (失败安全 IPR-0:
          压缩故障不得让 agent 崩溃, 退回原 LENGTH 终止路径)。
        - 压缩 0 条 → False (已无可压缩空间)。
        - 成功 → ContextManager 负责替换 self._messages、记 CONTEXT_COMPACTION
          到 timeline 并广播 observer 事件, 返回 True。

        本方法只压缩 model 看到的 messages; timeline (审计轨迹) 永不压缩 ——
        压缩本身反而是 timeline 上的一条 CONTEXT_COMPACTION 事件 (§9.2.9 不变量)。
        """
        return self._context_mgr._auto_compact(reason=reason)

    def _try_compact_for_continuation(self) -> bool:
        """max_steps 软处理: 到上限时压缩上下文, 给 agent 继续的机会。

        复用 _auto_compact (CONTEXT_COMPACTION 事件记 timeline)。
        返回 True 表示压缩发生且消息数减少; False 表示无法压缩 (已最简或失败)。
        """
        try:
            # M4 fix: 用 ChatState 作为唯一真相源 (self._messages 是向后兼容影子列表,
            # 压缩经 ChatState 更新后可能未同步, 用它判断会漏判压缩效果)。
            old_count = self._chat_state.message_count
            self._auto_compact(reason="max_steps_continuation")
            return self._chat_state.message_count < old_count
        except Exception:
            return False

    def _init_baseline_modified(self) -> None:
        """v1.1: 记录 run 启动时 git modified 文件数基线, 用于 anomaly 检测。
        
        如果工作区启动时就有大量 modified 文件, 不计入 run 期间的 anomaly。
        静默失败: git 不可用时不阻塞 run。
        """
        try:
            import subprocess
            result = subprocess.run(
                ["git", "diff", "--name-only", "HEAD"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=5,
                cwd=self._project_root,
            )
            if result.returncode == 0 and result.stdout.strip():
                files = [f for f in result.stdout.split("\n") if f.strip()]
                self._baseline_modified_files = len(files)
            # 将基线设置到世界模型
            if self._perception_engine is not None:
                wm = getattr(self._perception_engine, 'world_model', None)
                if wm is not None and hasattr(wm, 'set_baseline_modified'):
                    wm.set_baseline_modified(self._baseline_modified_files)
        except Exception:
            self._baseline_modified_files = 0

    # ── §3.4 GoalDowngrade: downgradeinit化 (v0.0.11) ──

    def _init_downgrade(self) -> None:
        """进入主循环前check是否需要 GoalDowngrade (§3.4)。

        流程:
          1. 若 _allow_downgrade=False, 跳过
          2. 尝试 suggest_downgrade (基于当前 Goal 的 GoalType)
          3. 若 strict=False (默认): 记录候选到 timeline, 保持原 Goal, 不打扰用户
          4. 若 strict=True: 询问用户 (通过闸门), 用户可选择候选

        v0.5.1: 默认非交互。降级候选仅记录到 timeline, 不弹出交互确认。
        仅在 strict 模式或用户显式 /goal 时启用交互降级。
        移除了 _is_trivial_task hack。
        """
        if not self._allow_downgrade:
            return

        baseline_sha = self._resolve_git_sha() or ""
        downgrade = GoalRefiner.suggest_downgrade(
            self._goal, baseline_git_sha=baseline_sha,
        )

        if downgrade is None:
            return  # 当前 GoalType 不适用降级

        # ── v0.5.1: 非 strict 模式 → 仅记录候选, 保持原 Goal, 不打扰用户
        if not self._strict:
            self._recorder.append(
                event_id=f"downgrade_skipped_{self._step_count}",
                ts=int(time.time() * 1000),
                event_type=EventType.GOAL_DOWNGRADE,
                payload={
                    "original_type": downgrade.original.statement.goal_type.value,
                    "action": "auto_skip_non_strict",
                    "candidate_count": len(downgrade.candidates),
                    "candidates": [
                        c.statement.goal_type.value for c in downgrade.candidates
                    ],
                },
            )
            return

        # ── strict 模式: 交互式降级 ──

        # construct一个假 action 用于gate交互 (downgrade是 Goal 层面，非tool层面)
        placeholder_action = Action(
            tool_id="__goal_downgrade__",
            args={
                "original_type": downgrade.original.statement.goal_type.value,
                "original_intent": downgrade.original.statement.intent,
                "candidates": [
                    c.statement.goal_type.value for c in downgrade.candidates
                ],
                "candidates_desc": [
                    {"index": i, "goal_type": c.statement.goal_type.value,
                     "description": c.statement.rewriting}
                    for i, c in enumerate(downgrade.candidates)
                ],
            },
        )
        dummy_judgement = Judgement(
            level=SafeLevel.GREYLIST,
            matched_rule_ids=("goal_downgrade",),
        )

        # v0.5.0 (C6 fix): 先询问用户, 再记录 GATE_DECISION 事件
        # 保证 timeline 顺序: user_response → gate_decision → downgrade_applied
        user_resp = self._user_responder.ask(placeholder_action, dummy_judgement)

        # 记录 USER_RESPONSE event (实际交互顺序)
        self._recorder.append(
            event_id=f"user_response_downgrade_{self._step_count}",
            ts=int(time.time() * 1000),
            event_type=EventType.USER_RESPONSE,
            payload={
                "response_type": user_resp.response_type.value,
            },
        )

        # 在用户响应后记录 GATE_DECISION (反映实际决策时序)
        self._gate_decision_count += 1
        self._recorder.append(
            event_id=f"gate_decision_{self._gate_decision_count}",
            ts=int(time.time() * 1000),
            event_type=EventType.GATE_DECISION,
            payload={
                "tool_id": placeholder_action.tool_id,
                "level": dummy_judgement.level.value,
                "matched_rules": list(dummy_judgement.matched_rule_ids),
            },
        )
        self._emit(LoopEvent(
            kind="gate_decision",
            step=self._step_count,
            payload={
                "tool_id": placeholder_action.tool_id,
                "args": dict(placeholder_action.args),
                "level": dummy_judgement.level.value,
                "matched_rules": list(dummy_judgement.matched_rule_ids),
            },
        ))

        if user_resp.response_type == UserResponseType.ACCEPT_DOWNGRADE:
            idx = max(0, min(user_resp.downgrade_index,
                             len(downgrade.candidates) - 1))
            chosen = downgrade.candidates[idx]

            # R4: original 永不remove
            self._original_goal = self._goal
            self._candidate_goals = downgrade.candidates
            self._downgrade_depth = downgrade.downgrade_depth

            # replace当前 Goal 为选中的 candidate
            self._goal = chosen

            self._final_claim = (
                f"downgrade: original={self._original_goal.statement.goal_type.value}"
                f"→candidate[{idx}]={chosen.statement.goal_type.value}"
            )

            # 记录downgradeevent到 timeline
            self._recorder.append(
                event_id=f"downgrade_{self._step_count}",
                ts=int(time.time() * 1000),  # 主循环开始前
                event_type=EventType.GOAL_DOWNGRADE,
                payload={
                    "original_type": self._original_goal.statement.goal_type.value,
                    "chosen_index": idx,
                    "chosen_type": chosen.statement.goal_type.value,
                    "downgrade_depth": self._downgrade_depth,
                    "candidate_count": len(downgrade.candidates),
                },
            )
        else:
            # REJECT_DOWNGRADE: 保持原 Goal, agent 继续以 UNDECIDABLE 运行
            self._final_claim = (
                f"downgrade rejected by user: "
                f"running with original={downgrade.original.statement.goal_type.value}"
            )

    def _emit_model_call_event(self, resp: ModelResponse) -> None:
        """§6.1 呈现层投影: broadcast model_call event给 observer (与 timeline 记录同 payload)。"""
        self._emit(LoopEvent(
            kind="model_call",
            step=self._step_count,
            payload={
                "model": self._model.model_name,
                "stop_reason": resp.stop_reason.value,
                "content": resp.content,
                "reasoning": resp.reasoning,
                "tool_calls": [
                    {"id": tc.id, "tool_id": tc.tool_id, "args": dict(tc.args)}
                    for tc in resp.tool_calls
                ],
                "usage": dict(resp.usage) if resp.usage else {},
            },
        ))

    def _call_model(self, *, emit_model_call: bool = True) -> ModelResponse:
        """调model, 记录到 RunRecorder (薄委托 → loop_model_call.call_model)。

        stream 分流/记录点/emit_model_call 语义见 loop_model_call 模块 docstring。
        抽取缘由: loop.py 瘦身 (同 loop_perception/loop_checkpoint 协作者模式);
        行为等价由 test_loop_stream_invariants / test_stream_error_invariants 守护。
        """
        from zall.core.loop_model_call import call_model
        return call_model(self, emit_model_call=emit_model_call)

    def _call_model_stream(self, tool_schemas: list[dict[str, Any]]) -> ModelResponse:
        """stream式调model (薄委托 → loop_model_call.call_model_stream)。"""
        from zall.core.loop_model_call import call_model_stream
        return call_model_stream(self, tool_schemas)

    # ── PR-0 contenthallucination扫描 (v0.0.11) ──

    @staticmethod
    def _scan_hallucinated_content(content: str) -> tuple[str, ...]:
        """PR-0: Scan STOP response for faked tool output patterns.

        实现已抽取到 core/hallucination.py (loop.py 瘦身, 架构评估 P0-item2);
        此处保留薄委托 staticmethod 以兼容内部调用与既有测试
        (AgentLoop._scan_hallucinated_content)。
        """
        from zall.core.hallucination import scan_hallucinated_content
        return scan_hallucinated_content(content)

    def _execute_tool_calls(self, tool_calls: tuple[ToolCall, ...]) -> None:
        """Execute tool calls via ToolExecutor (delegated)."""
        self._tool_executor.execute_all(tool_calls, self._step_count)

    # ── GitProtect security网 (v0.0.10) + CheckpointManager (v0.1.0) ──
    # Phase 2: replaced hardcoded _WRITE_TOOLS with ToolKind-based detection
    _WRITE_TOOLS: frozenset[str] = frozenset({"write_file", "edit_file", "batch_edit", "bash"})
    WRITE_TOOLS: frozenset[str] = _WRITE_TOOLS
    """公开写tool集合 (供 CLI /undo 等command使用)"""

    @classmethod
    def _is_write_tool_kind(cls, tool_id: str, loop: AgentLoop | None = None) -> bool:
        """Use ToolKind to check if a tool is a write tool.
        
        Falls back to _WRITE_TOOLS frozenset for tools that don't have kind set.
        """
        if tool_id in cls._WRITE_TOOLS:
            return True
        # Check via ToolRegistry if available
        if loop is not None and loop._tools is not None:
            from zall.core.tool import get_tool_kind
            tool = loop._tools.get(tool_id)
            if tool is not None:
                return get_tool_kind(tool).is_write()
        return False

    # B2: bash 写operation关键词 — 仅当command含这些关键词时才触发 checkpoint
    _BASH_WRITE_KEYWORDS: tuple[str, ...] = (
        ">", ">>", "| tee", "2>", "&>",
        "sed -i", "sed --in-place",
        "mv ", "cp ", "rm ", "mkdir ", "rmdir ", "touch ",
        "git add", "git commit", "git push", "git rm", "git mv",
        "python -c", "python3 -c",
        "pip install", "npm install", "yarn add",
        "make ", "cmake ", "gcc ", "g++ ", "rustc ",
        "chmod ", "chown ", "ln ", "dd ",
        "wget ", "curl -o", "curl --output",
        "unzip ", "tar ", "gzip ", "xz ",
        "docker ", "kubectl apply",
        "npx create", "npx --yes",
        "echo >", "printf >",
    )

    def _is_bash_write(self, action_args: dict[str, Any] | None) -> bool:
        """判断 bash command是否可能写filesystem (B2 optimize)。

        通过命令关键词启发式判断, 避免每次 bash 都触发 checkpoint。
        """
        if not action_args:
            return False
        command = action_args.get("command", "")
        cmd_lower = command.lower().strip()
        if not cmd_lower:
            return False
        for kw in self._BASH_WRITE_KEYWORDS:
            if kw.lower() in cmd_lower:
                return True
        return False

    def _maybe_checkpoint(self, tool_id: str, action_args: dict[str, Any] | None = None) -> None:
        """写operation后自动 checkpoint (git-native + file-based 双security网).

        GitProtect: git stash 安全点 (仅 git 仓库, 不污染 git log)
        CheckpointManager: 文件系统快照 (任何目录, 不依赖 git)

        B1: 传入 action_args 供 CheckpointManager 增量追踪,
        避免每次写操作都全量 os.walk 扫描项目目录。
        B2: bash 仅当命令含写操作关键词时才触发 checkpoint。

        静默失败: 安全网故障不得改变 RunEgress (IPR-0 反例).
        """
        if tool_id not in self._WRITE_TOOLS:
            return

        # B2: bash 需启发式判断是否真的写file
        if tool_id == "bash" and not self._is_bash_write(action_args):
            return

        # 1. GitProtect (git-native)
        if self._git_protect is not None:
            try:
                self._git_protect.checkpoint(label=f"step_{self._step_count}")
            except Exception as _gp_err:
                # 安全网故障不得改变 RunEgress (IPR-0 反例), 但须可观测 (不静默)
                _log.warning("GitProtect checkpoint failed (safety net degraded): %s", _gp_err)

        # 2. CheckpointManager (file-based, 不dependency git)
        self._maybe_checkpoint_file(tool_id, action_args)

    # ── file-based checkpoint 追踪 ──
    # 逻辑已抽取到 core/loop_checkpoint.py (loop.py 瘦身, 推荐 C / 架构评估 P0-item2)。
    # 常量 (追踪扩展名 / 敏感排除模式) 与 os.walk 扫描均迁至该模块; 此处保留薄委托
    # 方法, 兼容既有内部调用与既有测试 (test_plugin_safety 替换 _maybe_checkpoint)。
    def _get_or_init_tracked_cache(self) -> set[str] | None:
        """获取或初始化追踪文件缓存 (instance 级, 防跨 loop 复用旧 cache)。"""
        if self._cached_tracked_files is None:
            self._cached_tracked_files = self._scan_tracked_files()
        return self._cached_tracked_files

    def _invalidate_tracked_cache(self) -> None:
        """O3: 写操作后使追踪文件缓存失效, 下次访问重新扫描。"""
        self._cached_tracked_files = None

    def _maybe_checkpoint_file(self, tool_id: str, action_args: dict[str, Any] | None = None) -> None:
        """写操作后自动文件系统快照 (委托 loop_checkpoint, 逻辑见该模块)。"""
        loop_checkpoint.maybe_checkpoint_file(self, tool_id, action_args)

    def _get_checkpoint_files(self, tool_id: str, action_args: dict[str, Any] | None = None) -> set[str]:
        """获取本次 checkpoint 追踪文件列表 (委托 loop_checkpoint)。"""
        return loop_checkpoint.get_checkpoint_files(self, tool_id, action_args)

    def _scan_tracked_files(self) -> set[str]:
        """全量扫描项目目录返回追踪文件集合 (委托 loop_checkpoint)。"""
        return loop_checkpoint.scan_tracked_files(self)

    def _check_termination(self) -> RunEgress:
        """model STOP 后, check Goal termination。

        v0.0.10: 采集真实 git sha 作为 Evidence (替代占位 s0_baseline/s0_current)。
        v0.0.32: 每次终止检查前清除 git SHA 缓存, 确保捕获最新提交。
        v0.6.x: 多 Judge 编排 (§12.1 Accountability 三态编排, MASTER.md §12.1 表)。

        §5.4 consistency 规则 (MASTER.md §12.1):
          - 主 undecidable → 辅 cannot override
          - 主 not_met → 辅 cannot rescue
          - 主 met + 辅 met → met
          - 主 met + 辅 not_met/undecidable → met_with_caveat (main_aux_divergent)
        """
        # O1: 清除 git SHA cache, 确保 _resolve_git_sha return最新值
        self._cached_git_sha.clear()
        if self._judge is None and self._judges is None:
            self._emit(LoopEvent(
                kind="judge_result",
                step=self._step_count,
                payload={"state": "undecidable", "reason": "no judge"},
            ))
            return self._make_egress(TerminationState.UNDECIDABLE)

        from zall.core.accountability import Evidence, base_judge

        # v0.0.10: 采集真实 git sha (v0.0.6 fix H1: baseline_sha ≠ current_sha)
        current_sha = self._resolve_git_sha("HEAD")
        baseline_sha = self._run_start_sha or current_sha
        evidence = Evidence(
            baseline_sha=baseline_sha or "no_git",
            current_sha=current_sha or "no_git",
        )

        # §12.1 多 Judge 编排: 查 base_judge 表得到 main/aux judge_type
        goal_type = self._goal.statement.goal_type
        main_type, aux_type = base_judge(goal_type)

        # 从 config 解析 main_judge 和 aux_judge 实例
        main_judge = self._resolve_judge(main_type)
        aux_judge = self._resolve_judge(aux_type) if aux_type else None

        # 若 main_judge 为 None -> 走原有 undecidable 路径
        if main_judge is None:
            self._emit(LoopEvent(
                kind="judge_result",
                step=self._step_count,
                payload={
                    "state": "undecidable",
                    "reason": "no main judge",
                    "main_type": main_type,
                    "aux_type": aux_type,
                },
            ))
            return self._make_egress(TerminationState.UNDECIDABLE)

        main_verdict = main_judge(evidence)
        aux_verdict = aux_judge(evidence) if aux_judge is not None else None

        result = AccountabilityResult.from_verdicts(main_verdict, aux_verdict)

        # timeline recorder 事件
        recorder_payload: dict[str, Any] = {
            "state": result.state.value,
            "caveat": result.caveat.value if result.caveat else None,
            "main_type": main_type,
            "aux_type": aux_type,
            "main_state": main_verdict.state.value,
        }
        if aux_verdict is not None:
            recorder_payload["aux_state"] = aux_verdict.state.value

        self._recorder.append(
            event_id=f"judge_{self._step_count}",
            ts=int(time.time() * 1000),
            event_type=EventType.JUDGE_RESULT,
            payload=recorder_payload,
        )

        # LoopEvent (给 observer/event_bus)
        event_payload: dict[str, Any] = {
            "state": result.state.value,
            "caveat": result.caveat.value if result.caveat else None,
            "report": main_verdict.report,
            "main_type": main_type,
            "aux_type": aux_type,
            "main_state": main_verdict.state.value,
        }
        if aux_verdict is not None:
            event_payload["aux_state"] = aux_verdict.state.value

        self._emit(LoopEvent(
            kind="judge_result",
            step=self._step_count,
            payload=event_payload,
        ))

        # M2: anchor run tail before returning
        if self._anchor is not None:
            self._recorder.anchor_to(self._anchor, int(time.time() * 1000))

        return self._make_egress(result.state)

    def _resolve_judge(self, judge_type: str) -> Any | None:
        """从 config 解析指定 judge_type 的 Judge 实例 (§12.1 多 Judge 编排)。

        优先查 self._judges dict, 若 judge_type 匹配则返回;
        若 self._judges 为 None (向后兼容单 judge 模式), 直接返回 self._judge
        (不检查类型 -- 保持旧行为, 单 judge 充当所有 type)。
        P3 fix 仅在 _judges dict 模式下生效 (多 Judge 显式模式才查类型匹配)。
        """
        # 多 Judge 模式: 优先查 judges dict
        if self._judges is not None:
            if judge_type in self._judges:
                return self._judges[judge_type]
            # 多 Judge 模式下 fallback 到 judge (若类型匹配)
            if self._judge is not None and hasattr(self._judge, 'judge_type'):
                if self._judge.judge_type == judge_type:
                    return self._judge
            return None  # P3 fix: 多 Judge 模式下类型不匹配不返回
        # 向后兼容: 无 judges dict, 直接返回 self._judge (不检查类型)
        return self._judge

    def _resolve_git_sha(self, ref: str = "HEAD") -> str | None:
        """采集真实 git sha (v0.0.10), O6: cached to avoid repeated subprocess calls.

        纯函数: 不修改状态, 不调外部服务 (subprocess 是本机 git)。
        失败静默返回 None —— git 不可用不影响 agent 判定 (Evidence 非关键路径)。

        O6: results are cached per ref in self._cached_git_sha. Clear at run start.
        """
        if ref in self._cached_git_sha:
            return self._cached_git_sha[ref]
        import subprocess
        try:
            r = subprocess.run(
                ["git", "rev-parse", ref],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=5,
                cwd=self._project_root,
            )
            result = r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None
            self._cached_git_sha[ref] = result
            return result
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            self._cached_git_sha[ref] = None
            return None

    def _make_egress(
        self, state: TerminationState, error: str | None = None
    ) -> RunEgress:
        """construct RunEgress (S1: includes §3.4.5 downgrade fields)。"""
        return RunEgress(
            run_id=self._run_id,
            final_state=state,
            step_count=self._step_count,
            total_tool_calls=self._tool_call_count,
            total_model_calls=self._model_call_count,
            error=error,
            # §3.4.5 GoalDowngrade 报告义务
            original_goal=self._original_goal,
            candidate_goals=self._candidate_goals,
            downgrade_depth=self._downgrade_depth,
            final_claim=(
                self._final_claim
                if self._final_claim
                else f"run {self._run_id[:8]} completed with state={state.value}"
            ),
        )

    # ── §12.1 Verifiability: 运行时链完整性自检 (MASTER.md §12.1 + §3.1.4) ──

    def _check_chain_integrity(self) -> str | None:
        """验证 timeline 链完整性, 返回警告字符串或 None (链完整)。

        不阻止运行, 只标记 (IPR-0: 检测篡改, 不阻止运行)。
        链断裂时记录 CHAIN_BROKEN 事件到 timeline 并返回警告描述。
        IPR-3: stdlib only.
        """
        if not self._recorder.verify_chain():
            _log.warning("timeline chain BROKEN — possible tampering detected (§12.1)")
            self._recorder.append(
                event_id=f"chain_broken_{self._run_id}",
                ts=int(time.time() * 1000),
                event_type=EventType.CHAIN_BROKEN,
                payload={
                    "step": self._step_count,
                    "event_count": len(self._recorder),
                },
            )
            return (
                f"timeline chain integrity check FAILED: "
                f"{len(self._recorder)} events, "
                f"tail_hash={self._recorder.tail_hash[:16]}..."
            )
        _log.debug("timeline chain integrity verified (§12.1)")
        return None
