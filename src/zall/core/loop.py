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
import fnmatch
import os
import re
import time
from typing import Any, Callable
from uuid import uuid4



from zall.core.action import Action
from zall.core.accountability import AccountabilityResult
from zall.core.chat_state import ChatState
from zall.core.context import Context
from zall.core.events import EventBus
from zall.core.extension import ExtensionRegistry
from zall.core.gate import (
    UserResponder,
    UserResponseType,
)
from zall.core.goal import GoalTriple, TerminationState
from zall.core.model import (
    Message,
    ModelAdapter,
    ModelResponse,
    StopReason,
    ToolCall,
    ToolChoice,
)
from zall.core.refiner import GoalRefiner
from zall.core.safety import Judgement, RuleSet, SafeLevel
from zall.core.tool import ToolRegistry
from zall.core.verifiability import EventType, RunRecorder
from zall.core.compactor import Compactor
from zall.core.checkpoint import CheckpointManager
from zall._util import skip_noise_dirs
from zall._util.path import NOISE_DIRS
from zall.core.executor import ToolExecutor
from zall.core.context_manager import ContextManager

# ── Import from sibling modules (Phase 1 refactoring) ──
from zall.core.loop_config import AgentConfig, _GitProtectProtocol
from zall.core.loop_events import MAX_STEPS, LoopEvent, RunEgress, StepResult


# ── Helper: trivial task detection ──


def _is_trivial_task(user_raw: str) -> bool:
    """判断用户输入是否为极简任务 (问候/打招呼/简单查询)。

    v0.4.10: 用于跳过不必要的目标降级交互, 减少对 hello world 类任务的打扰。
    只匹配明确的问候/打招呼/极简打印, 不误伤 "implement feature X" 等3词任务。
    """
    if not user_raw or len(user_raw) > 60:
        return False
    cleaned = user_raw.strip().lower()
    # 问候/打招呼
    greetings = {"hi", "hello", "hey", "你好", "嗨", "哈喽", "test", "help"}
    if cleaned in greetings:
        return True
    # "print <word>" / "say <word>" / "echo <word>" 模式
    for prefix in ("print ", "say ", "echo "):
        if cleaned.startswith(prefix) and len(cleaned) < 30:
            return True
    return False


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
        **kwargs: Any,
    ) -> None:
        # O9: 统一归一化为 AgentConfig
        # Phase 3: 移除旧式离散参数, 仅接受 config: AgentConfig
        _config = config
        if _config is None:
            if kwargs:
                import warnings
                warnings.warn(
                    "Passing legacy discrete parameters to AgentLoop is deprecated. "
                    "Use AgentConfig instead: AgentLoop(..., config=AgentConfig(...)).",
                    DeprecationWarning, stacklevel=2,
                )
                _config = AgentConfig.from_kwargs(**kwargs)
            else:
                _config = AgentConfig()

        # stream/allow_downgrade/plan_mode 的最终默认值
        _stream = _config.stream if _config.stream is not None else False
        _allow_downgrade = _config.allow_downgrade if _config.allow_downgrade is not None else True
        _plan_mode = _config.plan_mode if _config.plan_mode is not None else False

        self._model = model
        self._tools = tools
        self._rules = rules
        self._goal = goal
        self._context = context
        self._user_responder = user_responder
        self._judge = _config.judge
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
        # GitProtect safety net: injected by CLI layer, core does not import tools/
        self._git_protect = _config.git_protect
        # CheckpointManager: filesystem snapshot safety net
        self._checkpoint_mgr = _config.checkpoint_mgr
        # plan_mode (§9.2.5 read-only posture) — write tools force greylist requiring confirmation.
        self._plan_mode = _plan_mode
        # §9.2.9 reactive auto-compact strategy (optional injection) via ContextManager.
        self._compactor = _config.compactor
        self._context_mgr = ContextManager(self, self._compactor)
        self._anchor = _config.anchor

        # Extension registry (Pi-style lifecycle hooks)
        self._ext_registry: ExtensionRegistry | None = _config.ext_registry

        # ToolExecutor: extracted tool execution orchestrator (v0.3.0)
        self._tool_executor = ToolExecutor(self)

        # ── v0.4.0: ChatState 集成 — Actor 模式消息管理 ──
        self._chat_state: ChatState | None = _config.chat_state
        # 向后兼容: 保持 _messages 作为原始列表
        self._messages: list[Message] = []

        self._run_id = uuid4().hex
        self._recorder = RunRecorder(self._run_id)
        # _messages 通过 messages property 访问 (见 messages())
        self._step_count = 0
        self._tool_call_count = 0
        self._model_call_count = 0
        self._gate_decision_count = 0
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
        # B3: get project root path from context, used for git commands
        self._project_root: str = context.cwd_meta.cwd_path if hasattr(context, 'cwd_meta') else "."
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
            # but they must be observable (silent pass → violates falsifiability).
            import logging as _zall_logging
            _zall_logging.getLogger("zall.core.loop").warning(
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
        """当前 model context (只读snapshot, 不可直接修改)。"""
        if self._chat_state is not None:
            return self._chat_state.messages
        return list(self._messages)

    @property
    def chat_state(self) -> ChatState | None:
        """ChatState 实例 (v0.4.0)。如果未启用, 可惰性创建。"""
        return self._chat_state

    def get_chat_state(self) -> ChatState:
        """获取或惰性创建 ChatState 实例。

        首次调用时基于当前 _messages 创建 ChatState。
        之后返回同一个实例。
        """
        if self._chat_state is None:
            self._chat_state = ChatState(messages=list(self._messages))
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
        """当前 plan_mode state。"""
        return self._plan_mode

    @property
    def compactor(self) -> Compactor | None:
        """当前 compactor (可能为 None)。"""
        return self._compactor

    def set_plan_mode(self, enabled: bool) -> None:
        """更新 plan_mode state (供 CLI /plan command使用)。"""
        self._plan_mode = enabled

    def set_messages(self, messages: list[Message]) -> None:
        """replace model context messagelist (供 /compact/CLI command使用)。

        IPR-0: 替换后 timeline 保留 (不删除已有事件), 但调用方应确保
        在 timeline 上追加 CONTEXT_COMPACTION 事件以维持可复现性。
        O1: 标记 token 估算缓存为脏。

        v0.4.8: 当 ChatState 启用时, 委托给 ChatState.replace_messages()。
        v0.4.9: 修复外部引用别名 — 创建副本而非直接引用输入列表。
        """
        if self._chat_state is not None:
            self._chat_state.replace_messages(messages)
        # 创建副本避免外部列表修改意外影响内部状态
        self._messages = list(messages)
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

        v0.4.8: 当 ChatState 启用时, 委托给 ChatState.remove_by_predicate()。
        v0.4.9: 修复 ChatState 启用时 _messages 不同步的 Bug。
        """
        if self._chat_state is not None:
            removed = self._chat_state.remove_by_predicate(predicate)
            # 从 ChatState 同步回 _messages, 保证两者一致
            self._messages = list(self._chat_state.messages)
            self._mark_watermark_dirty()
            return removed
        before = len(self._messages)
        self._messages = [m for m in self._messages if not predicate(m)]
        self._mark_watermark_dirty()
        return before - len(self._messages)

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

        # ── §3.4 GoalDowngrade: 进入主循环前checkdowngrade
        self._init_downgrade()

        # v0.0.6 fix (H1): 捕获运行开始时的 git SHA, 用于 Evidence compare
        self._run_start_sha = self._resolve_git_sha("HEAD")

        # init化: system prompt + user_raw 作为首条 user message
        self._messages = []
        if self._chat_state is not None:
            self._chat_state.reset()
        if system_prompt:
            self._append_message(Message(role="system", content=system_prompt))
        self._append_message(Message.user(self._context.user_raw))

        # Extension: on_agent_start (legacy) + on_turn_start (typed)
        if self._ext_registry is not None:
            from zall.core.lifecycle import TurnStartInput
            _ts_input = TurnStartInput(
                goal=self._goal,
                model_name=getattr(self._model, "model_name", ""),
                messages=list(self._messages),
                tools=self._tools.tools if self._tools else (),
                step=0,
            )
            self._ext_registry.fire_all(
                "on_agent_start", "on_turn_start",
                typed_input=_ts_input,
                goal=self._goal,
                model=self._model,
                messages=list(self._messages),
            )

        while True:
            result = self.step()
            if result.is_terminal:
                if result.egress is None:
                    raise RuntimeError("terminal StepResult must have non-None egress")
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
                return result.egress
            if result.kind == "awaiting_input":
                # task mode: model STOP → check Goal termination → return RunEgress
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
                return egress
            # tool_used → 继续循环

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

        try:
            # ── 0. §9.2.9 主动水位monitor (delegated to ContextManager)
            self._context_mgr.check_watermark_before_call(
                self._messages, self._model.model_name, self._step_count,
            )

            # ── 1. 调model (首次: 暂不broadcast model_call 渲染, 防 nudge 双重显示)
            resp = self._call_model(emit_model_call=False)
            self._model_call_count += 1

            # v0.0.21 空 STOP backoff: model空reply (不调tool也不回答) → inject nudge retry一次。
            # 仅 STOP + 空 content 触发; retry (emit_model_call=True) 渲染retry结果。
            # 限 1 次/step, 不循环; 持续空 → 落到下方正常 dispatch (诚实显示空/fallback)。
            if self._context_mgr.is_empty_stop(resp):
                self._context_mgr.handle_empty_stop(
                    self._messages, self._model_call_count, self._step_count,
                )
                resp = self._call_model()  # emit_model_call=True: 渲染重试结果
                self._model_call_count += 1
            else:
                # 非 nudge: 补发首次 model_call 渲染event (停 spinner + 显示结果)
                self._emit_model_call_event(resp)

            # ── 2. 停车条件
            if resp.stop_reason == StopReason.LENGTH:
                # §9.2.9 反应式 auto-compact: window爆 → 压缩 model context 后retry一次。
                # 反应式 (而非预测式) 是 PR-3 model-agnostic的直接推论: zall 不预设各model的
                # 确切window大小, 靠model自报 LENGTH 触发压缩, 天然model-agnostic。
                # timeline 全保真 (§6.1): 压缩只影响 model 看到什么, audit轨迹不丢。
                if self._auto_compact(reason="model_length"):
                    resp = self._call_model()
                    self._model_call_count += 1
                # 压缩后仍 LENGTH (或无 compactor / 已无可压缩) → 诚实terminate
                if resp.stop_reason == StopReason.LENGTH:
                    self._emit(LoopEvent(kind="length_exceeded", step=self._step_count,
                                         payload={"error": "context length"}))
                    return StepResult(
                        kind="terminal",
                        egress=self._make_egress(
                            TerminationState.UNDECIDABLE,
                            error="model returned LENGTH; context compaction "
                                  "could not reduce further",
                        ),
                    )
                # 压缩后 resp 变为 STOP / TOOL_USE → 落到下方正常handlepath

            if resp.stop_reason == StopReason.STOP:
                # P0 fix: 检测false装成 STOP 的 API error (adapters/base.py make_error_response)
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
                # model说完了
                # ── PR-0 自证false: 扫描 STOP reply是否false造了tooloutput
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
                # 把 assistant reply加入 messages (对话pattern需要, taskpattern也无害)
                self._append_message(Message.assistant(content=resp.content))
                # taskpattern: check Goal termination → terminal
                # 对话pattern: 不terminate, return awaiting_input
                # step() 不知道自己是task还是对话 → return awaiting_input,
                # run() 会在收到 STOP 后调 _check_termination judgment (见下)
                return StepResult(kind="awaiting_input", content=resp.content)

            if resp.stop_reason == StopReason.TOOL_USE:
                if not resp.tool_calls:
                    # PR-0: stop_reason=TOOL_USE 但无 tool_calls → hallucination
                    err = ("stop_reason=TOOL_USE but tool_calls is empty — "
                           "model hallucinated tool use (PR-0 violation)")
                    self._emit(LoopEvent(kind="error", step=self._step_count,
                                         payload={"error": err}))
                    return StepResult(
                        kind="terminal",
                        egress=self._make_egress(TerminationState.UNDECIDABLE, error=err),
                    )

                self._execute_tool_calls(resp.tool_calls)
                self._append_message(
                    Message.assistant(content=resp.content, tool_calls=resp.tool_calls)
                )
                return StepResult(
                    kind="tool_used",
                    tools_used=tuple(tc.tool_id for tc in resp.tool_calls),
                )

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
        """内部追加消息, 当 ChatState 启用时同步更新。

        v0.4.8: 统一内部消息追加路径, 避免 ChatState 和 _messages 不同步。
        同时维护 self._messages (model call 直接使用) 和 ChatState (事件记录 + 快照)。
        """
        if self._chat_state is not None:
            # 通过 ChatState 的 push_* 方法保证事件记录
            if msg.role == "user":
                self._chat_state.push_user_message(msg.content)
            elif msg.role == "assistant":
                self._chat_state.push_assistant_response(
                    msg.content, tool_calls=msg.tool_calls or ()
                )
            elif msg.role == "tool":
                self._chat_state.push_tool_result(
                    msg.tool_call_id or "",
                    msg.content,
                    tool_id=msg.tool_id or "",
                )
            elif msg.role == "system":
                self._chat_state.push_system_message(msg.content)
        # self._messages 始终保持最新 (model call 直接使用此列表)
        self._messages.append(msg)

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
        return egress

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

    # ── §3.4 GoalDowngrade: downgradeinit化 (v0.0.11) ──

    def _init_downgrade(self) -> None:
        """进入主循环前check是否需要 GoalDowngrade (§3.4)。

        流程:
          1. 若 _allow_downgrade=False, 跳过
          2. 对极短简单任务 (≤3词, 无代码相关字符) 直接跳过降级, 减少无意义交互
          3. 尝试 suggest_downgrade (基于当前 Goal 的 GoalType)
          4. 若有候选 → 询问用户 (通过闸门)
          5. 用户接受 → 替换 _goal, 记录降级状态 (R4/R5/R6)
          6. 用户拒绝 → 保持原 Goal (走 UNDECIDABLE 路径)

        v0.4.10: 跳过极简单任务 (问候/打招呼/简单查询) 的目标降级,
        避免 "hello world" 类任务出现不必要的降级弹窗。
        """
        if not self._allow_downgrade:
            return

        # 极短简单任务直接跳过降级 (≤3词, 无代码相关字符)
        user_raw = self._context.user_raw.strip()
        if _is_trivial_task(user_raw):
            return

        baseline_sha = self._resolve_git_sha() or ""
        downgrade = GoalRefiner.suggest_downgrade(
            self._goal, baseline_git_sha=baseline_sha,
        )

        if downgrade is None:
            return  # 当前 GoalType 不适用降级

        # ── gate: ask用户是否acceptdowngrade

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

        # 补记 GATE_DECISION event (参考正常 gate stream程在 _process_gate 中的写法)
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

        user_resp = self._user_responder.ask(placeholder_action, dummy_judgement)

        # M2: record USER_RESPONSE event
        self._recorder.append(
            event_id=f"user_response_downgrade_{self._step_count}",
            ts=int(time.time() * 1000),
            event_type=EventType.USER_RESPONSE,
            payload={
                "response_type": user_resp.response_type.value,
            },
        )

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
        """调model, 记录到 RunRecorder。

        stream 分流 (P2):
          self._stream=True 且 adapter 有 complete_stream → 流式分支
          否则 → 阻塞 complete() (P1 行为, 零变化)

        流式语义 ≡ 阻塞: 最终 ModelResponse 一致, 记录点一致,
        只是过程中逐 token 广播 model_token 事件给 observer。

        emit_model_call (v0.0.21c): 默认 True 广播 model_call 渲染事件;
          False 时只记 timeline + model_call_start (spinner), 不广播 model_call
          渲染。供 step() 的"首次调用"用 —— 防 nudge 重试时第一次空回复被渲染成
          "(empty)" 与重试结果双重显示。调用方在确认不需 nudge 后补发渲染。
        """
        # O2: use cached tool schemas (avoid rebuilding every model call)
        tool_schemas: list[dict[str, Any]] = self._tool_schemas

        # Extension: on_before_model
        if self._ext_registry is not None:
            self._ext_registry.fire(
                "on_before_model",
                messages=list(self._messages),
                step=self._step_count,
            )

        # §6.1 呈现层投影: 调model前broadcast model_call_start (让呈现层显示 spinner)
        # 纯 observer event, 不进 RunRecorder (start 不是auditevent, 完成才记)
        self._emit(LoopEvent(
            kind="model_call_start",
            step=self._step_count,
            payload={"model": self._model.model_name},
        ))

        if self._stream:
            resp = self._call_model_stream(tool_schemas)
        else:
            resp = self._model.complete(
                messages=self._messages,
                tools=tool_schemas,
                tool_choice=ToolChoice.AUTO,
            )

        # 记录 model_call event (stream式/blocking共用同一record point)
        # §6.2 replay 要求 timeline 存完整 ModelResponse (不只digest)
        # B2 fix: 同时存储真实 usage 数据, 供 /undo 校正使用
        self._recorder.append(
            event_id=f"model_call_{self._model_call_count}",
            ts=int(time.time() * 1000),
            event_type=EventType.MODEL_CALL,
            payload={
                "model": self._model.model_name,
                "stop_reason": resp.stop_reason.value,
                "content_length": len(resp.content),
                "tool_calls_count": len(resp.tool_calls),
                # §6.2 replay 用: 完整response数据 (让 timeline reproducible)
                "content": resp.content,
                "reasoning": resp.reasoning,
                "reasoning_length": len(resp.reasoning),
                "tool_calls": [
                    {"id": tc.id, "tool_id": tc.tool_id, "args": dict(tc.args)}
                    for tc in resp.tool_calls
                ],
                # B2: 真实 usage 数据, 供 _recalc_usage_from_timeline 使用
                "usage": dict(resp.usage) if resp.usage else {},
            },
        )
        # §6.1 呈现层投影: 同一record pointbroadcast给 observer
        if emit_model_call:
            self._emit_model_call_event(resp)

        return resp

    def _call_model_stream(self, tool_schemas: list[dict[str, Any]]) -> ModelResponse:
        """stream式调model, 逐 token broadcast, return最终 ModelResponse。

        语义 ≡ 阻塞: 最终返回的 ModelResponse 与 complete() 等价。
        过程中每个 token 通过 observer 广播 model_token 事件 (呈现层用)。
        RunRecorder 不记 token (那是呈现层, 不是审计轨迹)。
        """
        resp: ModelResponse | None = None
        # 思考过程分stream (§9.2.12): model先给 reasoning 再给 content。
        # 用长度增量judgment当前 token 属于哪条通道 (reasoning 阶段 content 不增长),
        # 不引入新interface (仍沿用 complete_stream 的 (token, accumulated) protocol)。
        prev_content_len = 0
        prev_reasoning_len = 0
        # Track tool call count to detect new tool call deltas
        prev_tool_call_count = 0
        try:
            for token, accumulated in self._model.complete_stream(  # type: ignore[attr-defined]
                messages=self._messages,
                tools=tool_schemas,
                tool_choice=ToolChoice.AUTO,
            ):
                if token:
                    reasoning = accumulated.reasoning
                    if (len(reasoning) > prev_reasoning_len
                            and len(accumulated.content) == prev_content_len):
                        # 思考过程增量 → model_thinking (呈现层透明展示)
                        delta = reasoning[prev_reasoning_len:]
                        self._emit(LoopEvent(
                            kind="model_thinking",
                            step=self._step_count,
                            payload={"token": delta, "accumulated": reasoning},
                        ))
                        prev_reasoning_len = len(reasoning)
                    else:
                        # content增量 → model_token (呈现层stream式显示)
                        self._emit(LoopEvent(
                            kind="model_token",
                            step=self._step_count,
                            payload={"token": token, "accumulated": accumulated.content},
                        ))
                        prev_content_len = len(accumulated.content)
                # Tool call delta: emit model_tool_call event so UI can show progress
                if accumulated.tool_calls and len(accumulated.tool_calls) > prev_tool_call_count:
                    # Only emit when new tool calls appear (not on every token)
                    prev_tool_call_count = len(accumulated.tool_calls)
                    self._emit(LoopEvent(
                        kind="model_tool_call",
                        step=self._step_count,
                        payload={
                            "tool_calls": [
                                {"id": tc.id, "tool_id": tc.tool_id, "args": dict(tc.args)}
                                for tc in accumulated.tool_calls
                            ],
                        },
                    ))
                resp = accumulated
        except GeneratorExit:
            # GeneratorExit must重抛 (Python generatorprotocol: 关闭信号不可吞)
            # 吞掉会破坏 with/finally cleanup链, 导致资源leak
            raise
        except Exception:
            # 其他stream式exception → downgrade为 STOP (失败security)
            if resp is None:
                return ModelResponse(content="", stop_reason=StopReason.STOP)
            return resp
        # stream式结束, resp 是最终 ModelResponse (含完整 content + tool_calls + stop_reason)
        if resp is None:
            # stream式没产出任何东西 (exception) → downgrade为 STOP
            return ModelResponse(content="", stop_reason=StopReason.STOP)
        return resp

    # ── PR-0 contenthallucination扫描 (v0.0.11) ──

    # modelfalse造tooloutput的常见pattern (预编译正则, 避免每次 STOP 都编译)
    _HALLUCINATION_RE: tuple[tuple[re.Pattern[str], str], ...] = (
        (re.compile(r"\$\s+(?:sudo|apt|pip|npm|git|python|node|cd|ls|cat|cp|mv|rm|mkdir|chmod|echo)\b"), "fake_bash_prompt"),
        (re.compile(r"\w+@\w+:~[/\w]*\$"), "fake_user_host_prompt"),
        (re.compile(r"---\s*(?:BEGIN|START|END)\s*(?:FILE|CONTENT)?\s*---"), "fake_file_delimiter"),
        (re.compile(r"\b\d+\s*(?:bytes|KB|MB)\s+(?:written|read|modified|saved|created)\b"), "fake_file_size_report"),
        # Tightened: requires @@ hunk header followed by +/- lines to avoid false
        # positives on bullet points, markdown lists, and negative numbers.
        (re.compile(r"(?m)^@@.*\n[\s\S]*?^(?:\+|\-)[^+\-]"), "fake_diff_block"),
        (re.compile(r"HTTP/\d\.\d\s+\d{3}"), "fake_http_response"),
        (re.compile(r"<tool_output>"), "fake_tool_output_xml"),
        (re.compile(r"<function_call>"), "fake_function_call_xml"),
    )

    @classmethod
    def _scan_hallucinated_content(cls, content: str) -> tuple[str, ...]:
        """PR-0: Scan STOP response for faked tool output patterns."""
        found: list[str] = []
        for pattern, label in cls._HALLUCINATION_RE:
            if pattern.search(content):
                found.append(label)
        return tuple(found)

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
            except Exception:
                pass  # 安全网故障不得改变 RunEgress (IPR-0 反例)

        # 2. CheckpointManager (file-based, 不dependency git)
        self._maybe_checkpoint_file(tool_id, action_args)

    # skipnoisedirectory (v0.0.6 fix H4, v0.1.1: 使用 os.walk 提前filter)
    # v0.1.4: 统一为 _util/path.py 的 NOISE_DIRS + .zall (checkpoint directory)
    _SKIP_DIRS: frozenset[str] = frozenset(NOISE_DIRS | {".zall"})
    _TRACKED_EXTS: frozenset[str] = frozenset({
        ".py", ".js", ".ts", ".md", ".toml", ".yaml", ".yml",
        ".json", ".css", ".html", ".rs", ".go", ".java",
    })
    # v0.1.3: 排除敏感filepattern (secret leak防护)
    _EXCLUDE_PATTERNS: tuple[str, ...] = (
        ".env", ".env.*", "*.pem", "*.key", "*.cert",
        "*secret*", "*password*", "*credential*",
        "id_rsa", "id_ed25519", "*.pub",
    )

    # B1: cache全量扫描结果, 避免每次写operation都 os.walk
    # instance级cache (非class级): 多个 AgentLoop instance不共享, 防 /clear 后新 loop 用旧cache
    # O3: 写operation后cache失效, 下次访问重新扫描 (确保新file被trace)
    def _get_or_init_tracked_cache(self) -> set[str] | None:
        """获取或init化tracefilecache。"""
        if self._cached_tracked_files is None:
            self._cached_tracked_files = self._scan_tracked_files()
        return self._cached_tracked_files

    def _invalidate_tracked_cache(self) -> None:
        """O3: 写operation后使tracefilecache失效, 下次访问重新扫描。"""
        self._cached_tracked_files = None

    def _maybe_checkpoint_file(self, tool_id: str, action_args: dict[str, Any] | None = None) -> None:
        """写operation后自动filesystemsnapshot (CheckpointManager, v0.1.0).

        B1 优化:
          - 首次调用: 全量 os.walk 扫描, 缓存结果到 _cached_tracked_files
          - 后续调用: 从工具参数提取文件路径, 仅追踪本次修改的文件
          - bash 等无法获知具体文件的工具: 使用缓存的全量列表

        O3 增量 cache 策略:
          - write_file/edit_file/batch_edit (已知路径): 直接加入 cache, 避免全量扫描
          - bash (未知路径): 使 cache 失效, 下次全量扫描

        静默失败: 安全网故障不得改变 RunEgress (IPR-0 反例).
        """
        if self._checkpoint_mgr is None:
            return
        try:
            tracked = self._get_checkpoint_files(tool_id, action_args)
            if not tracked:
                return

            self._checkpoint_mgr.save_checkpoint(
                label=f"step_{self._step_count}_{tool_id}",
                files=tracked,
                tool_id=tool_id,
                run_id=self._recorder.run_id,
            )
            # O3: 增量 cache 策略 — 已知路径直接加入, 未知路径才全量扫描
            if tool_id in ("write_file", "edit_file", "batch_edit") and self._cached_tracked_files is not None:
                # 已知路径: 增量加入 cache, 避免全量 os.walk
                self._cached_tracked_files.update(tracked)
            else:
                # bash 等未知路径: 使 cache 失效, 下次重新扫描
                self._cached_tracked_files = None
        except Exception:
            pass  # 安全网故障不得改变 RunEgress

    def _get_checkpoint_files(self, tool_id: str, action_args: dict[str, Any] | None = None) -> set[str]:
        """获取本次 checkpoint 需要trace的filelist.

        B1 优化:
          - write_file/edit_file: 从工具参数提取路径, 增量追踪 (O(1))
          - batch_edit: 从 edits 列表提取每个 path
          - bash: 使用缓存的全量扫描结果 (O(n) 仅首次)
        """
        # 从toolparameter提取path (write_file/edit_file 等明确path的tool)
        if action_args:
            path = action_args.get("path") or action_args.get("file_path") or ""
            if path:
                return {path.replace("\\", "/")}
            # batch_edit: edits list含多个 path
            if tool_id == "batch_edit":
                edits = action_args.get("edits", [])
                if edits and isinstance(edits, list):
                    paths = set()
                    for ed in edits:
                        p = ed.get("path", "") if isinstance(ed, dict) else ""
                        if p:
                            paths.add(p.replace("\\", "/"))
                    if paths:
                        return paths

        # cache未init化 → 全量扫描 (仅首次)
        if self._cached_tracked_files is None:
            self._cached_tracked_files = self._scan_tracked_files()

        return self._cached_tracked_files or set()

    def _scan_tracked_files(self) -> set[str]:
        """全量扫描项目directory, returntracefile集合 (仅首次调用, 结果cache到instance)."""
        if self._checkpoint_mgr is None:
            return set()
        root = self._checkpoint_mgr.project_root
        candidates: list[str] = []
        if root.is_dir():
            for dirpath, dirnames, filenames in os.walk(str(root), topdown=True):
                # 提前filternoisedirectory (不遍历!)
                skip_noise_dirs(dirnames)
                rel_base = os.path.relpath(dirpath, str(root))
                for fn in filenames:
                    ext = os.path.splitext(fn)[1].lower()
                    if ext in self.__class__._TRACKED_EXTS:
                        rel = os.path.join(rel_base, fn) if rel_base != "." else fn
                        rel_norm = rel.replace("\\", "/")
                        # S1: 排除敏感文件模式 (fnmatch 同时匹配 .env 和 key.pem)
                        if any(fnmatch.fnmatch(rel_norm, pat) for pat in self.__class__._EXCLUDE_PATTERNS):
                            continue
                        candidates.append(rel_norm)

        # deterministic性sort后return完整集合 (不再硬限 100 个file, fix B9)
        candidates.sort()
        return set(candidates)

    def _check_termination(self) -> RunEgress:
        """model STOP 后, check Goal termination。

        v0.0.10: 采集真实 git sha 作为 Evidence (替代占位 s0_baseline/s0_current)。
        v0.0.32: 每次终止检查前清除 git SHA 缓存, 确保捕获最新提交。
        """
        # O1: 清除 git SHA cache, 确保 _resolve_git_sha return最新值
        self._cached_git_sha.clear()
        if self._judge is None:
            self._emit(LoopEvent(
                kind="judge_result",
                step=self._step_count,
                payload={"state": "undecidable", "reason": "no judge"},
            ))
            return self._make_egress(TerminationState.UNDECIDABLE)

        from zall.core.accountability import Evidence

        # v0.0.10: 采集真实 git sha (v0.0.6 fix H1: baseline_sha ≠ current_sha)
        current_sha = self._resolve_git_sha("HEAD")
        baseline_sha = self._run_start_sha or current_sha
        evidence = Evidence(
            baseline_sha=baseline_sha or "no_git",
            current_sha=current_sha or "no_git",
        )
        verdict = self._judge(evidence)

        result = AccountabilityResult.from_verdicts(verdict)

        self._recorder.append(
            event_id=f"judge_{self._step_count}",
            ts=int(time.time() * 1000),
            event_type=EventType.JUDGE_RESULT,
            payload={
                "state": result.state.value,
                "caveat": result.caveat.value if result.caveat else None,
            },
        )
        self._emit(LoopEvent(
            kind="judge_result",
            step=self._step_count,
            payload={
                "state": result.state.value,
                "caveat": result.caveat.value if result.caveat else None,
                "report": verdict.report,
            },
        ))

        # M2: anchor run tail before returning
        if self._anchor is not None:
            self._recorder.anchor_to(self._anchor, int(time.time() * 1000))

        return self._make_egress(result.state)

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
                capture_output=True, text=True, timeout=5,
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
