"""zall.tools.spawn_subagent — Subagent 生成tool (DESIGN.md §4.2, §9.2.10).

对应 DESIGN.md:
  §4.2  工具层: 8 核心工具之一 (spawn_subagent)
  §9.2.10 Subagent Authority 继承协议 (v0.0.14 交付: parent 规则继承 + 子代理更严格)
  §9.2.10 Team Mode (v0.3.0): 线程级并行子 agent

核心功能:
  主 agent 生成子 agent 执行独立子任务。
  子 agent 继承主 agent 的工具集但 Authority 更严格。
  返回子 agent 的执行结果给主 agent。

Minimal runnable (PR-1):
  - 同步执行 (默认, 向后兼容)
  - 线程级并行执行 (parallel=True, 不阻塞主 agent)
  - 限制 MAX_SUBAGENT_STEPS=10 (防 runaway)
  - 子 agent 的 bash 写入操作走 GREYLIST (即使主 agent 是 whitelist)
  - 子 agent 不能再次 spawn subagent (防无限嵌套)
  - 子 agent 的结果作为 tool_result 回灌主 agent
  - list_subagents 工具查询运行中/已完成子 agent 状态

IPR constraints:
  IPR-0: 测试在 tests/test_edit_bash_invariants.py (edit/batch/spawn 属同批)
  IPR-3: 不 import 模型 SDK (通过 AgentLoop 注入)
  IPR-4: 本文件是 tool primitive, 不是主 Loop
"""

from __future__ import annotations

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from zall.core.action import Action
from zall.core.context import Context
from zall.core.gate import (
    UserResponse,
    UserResponseType,
)
from zall.core.goal import (
    AcceptanceContract,
    GoalStatement,
    GoalTriple,
    GoalType,
    TerminationState,
)
from zall.core.loop import AgentLoop
from zall.core.safety import Judgement, Rule, RuleSet, SafeLevel
from zall.core.tool import ToolRegistry, ToolResult


# ──────────────────────────────────────────────────────────────────────────
# 子 agent Authority: 更严格的securityrule
# ──────────────────────────────────────────────────────────────────────────


def _build_subagent_rules(parent: RuleSet | None, write_access: bool = False) -> RuleSet:
    """construct子 agent 的 Authority: 继承 parent + 更严格收紧 (§9.2.10).

    §9.2.10 核心要求: 子 agent **继承 parent 的 Authority 约束**,
    防 parent blacklist (eg. rm -rf) 后 spawn subagent 绕道越界 (偷渡风险).
    同时子 agent Authority 必须**更严格** (R6 不可单方触发, DESIGN.md §3.4.3).

    合并策略 (守 context_judge 优先级链 DENY > GREY > WHITE, §4.2.1):
      1. 继承 parent.core_deny_rules (最强, 防绕过) — 直接作子 agent core_deny.
      2. 继承 parent.user_local_rules + parent.domain_rules (parent 自定义约束).
      3. 叠加子 agent 收紧规则 (override 更严格):
         - spawn_subagent → BLACKLIST (防递归嵌套 / 权限升级)
         - bash / write_file / edit_file → GREYLIST (除非 write_access=True)

    不变量:
      - 子 agent 永远不比 parent 更宽松 (收紧只增不减).
      - parent 的 blacklist 必被继承 (绕道防护).
    """
    tightening: list[Rule] = [
        Rule(
            rule_id="subagent_ban_spawn",
            tool_id_pattern="spawn_subagent",
            level=SafeLevel.BLACKLIST,
        ),
    ]
    if not write_access:
        for tid in ("bash", "write_file", "edit_file"):
            tightening.append(
                Rule(
                    rule_id=f"subagent_grey_{tid}",
                    tool_id_pattern=tid,
                    level=SafeLevel.GREYLIST,
                )
            )

    if parent is None:
        # set_context 未inject (exceptionpath) — 退化为仅收紧rule
        return RuleSet(user_local_rules=tuple(tightening))

    return RuleSet(
        core_deny_rules=parent.core_deny_rules,
        user_local_rules=parent.user_local_rules + tuple(tightening),
        domain_rules=parent.domain_rules,
    )


# ──────────────────────────────────────────────────────────────────────────
# 子 agent tool集: 继承 parent (含 MCP), 排除 spawn_subagent (§9.2.11)
# ──────────────────────────────────────────────────────────────────────────


def _build_subagent_tools(parent_tools: ToolRegistry) -> ToolRegistry:
    """construct子 agent tool集 (§9.2.11 子 agent 继承 MCP tool).

    子 agent 继承 parent 的**完整** registry (含通过 §9.2.11 注册的 MCP 工具),
    但排除 spawn_subagent 自身 —— 防递归嵌套:
      - 不暴露 spawn schema → 省 token + 模型不会尝试再生成子 agent
      - 比只靠 _build_subagent_rules 的 spawn BLACKLIST 兜底更干净 (双保险仍在)

    MCP 工具的 Authority 不在这里决定, 而由 _build_subagent_rules 继承 parent 规则:
      - 默认 greylist (deny-by-default, §9.2.11) → 子 agent 无监督时 _SubagentResponder
        自动 reject (安全: 无人看管的子 agent 不擅自跑 MCP 副作用工具);
      - parent 显式 whitelist 的 (只读) MCP 工具 → 子 agent 继承 whitelist → 可用。
    这守住 §9.2.10 继承语义: 子 agent 永不比 parent 更宽松。

    不变量:
      - 子 agent 工具集 ⊆ parent 工具集 (只减不增, 绝不凭空获得新能力)。
      - spawn_subagent 必被排除 (防无限嵌套)。
    """
    return ToolRegistry(tools=tuple(
        t for t in parent_tools.tools if t.tool_id != "spawn_subagent"
    ))


# ──────────────────────────────────────────────────────────────────────────
# 子 agent 的 user responder: 自动 reject 所有质疑
# ──────────────────────────────────────────────────────────────────────────


class _SubagentResponder:
    """子 agent 的 user responder: 自动reject所有confirmrequest。

    子 agent 不应阻塞等待用户输入 (没人监视它),
    所有 greylist 自动 reject, blacklist 自动 reject,
    返回错误给子 agent 让它调整策略。
    """

    __test__ = False

    def ask(self, action: Action, judgement: Judgement) -> UserResponse:
        if judgement.level == SafeLevel.BLACKLIST:
            return UserResponse(
                response_type=UserResponseType.REJECT,
            )
        # whitelist 不会到这里 (ConfirmGate 直接 EXECUTING)
        # greylist 自动 reject (子 agent 无交互authority)
        return UserResponse(response_type=UserResponseType.REJECT)

    def __repr__(self) -> str:
        return "_SubagentResponder(auto_reject)"


# ──────────────────────────────────────────────────────────────────────────
# 子 agent 系统提示构建
# ──────────────────────────────────────────────────────────────────────────


def _build_subagent_prompt(subagent_type: str, write_access: bool = False) -> str:
    """根据子 agent 类型构建系统提示。

    Args:
        subagent_type: 子 agent 类型 (general-purpose / explore / plan)
        write_access: 是否允许写文件

    Returns:
        系统提示字符串
    """
    if subagent_type == "explore":
        return (
            "You are an EXPLORE sub-agent. Your ONLY job is to read and explore the codebase.\n"
            "You have read-only tools. You CANNOT modify files or run write commands.\n"
            "Use read_file, grep, glob, list_dir, and search tools to investigate.\n"
            "Report your findings concisely. Do not ask questions.\n"
            "When done, stop and summarize what you found."
        )

    if subagent_type == "plan":
        return (
            "You are a PLAN sub-agent. Your ONLY job is to analyze and design.\n"
            "You have read-only tools plus a todo list for tracking plans.\n"
            "You CANNOT modify files or run shell commands.\n"
            "Explore the codebase, understand the current state, and design a plan.\n"
            "Output your analysis clearly: Understanding -> Analysis -> Proposed Changes.\n"
            "Do not ask questions. When done, stop and present your plan."
        )

    # general-purpose (默认)
    if write_access:
        return (
            "You are a sub-agent of the main coding agent. "
            "You have write access to files. Complete the delegated task "
            "using available tools, then report your results. "
            "Do not ask questions (you have no user interaction)."
        )
    else:
        return (
            "You are a sub-agent of the main coding agent. "
            "Your task is delegated by the main agent. "
            "You may read files, search code, and run commands to complete the task. "
            "Write operations (write_file, edit_file) are restricted. "
            "Do not ask questions (you have no user interaction). "
            "When done, stop and report your findings."
        )


# ──────────────────────────────────────────────────────────────────────────
# SpawnSubagentTool
# ──────────────────────────────────────────────────────────────────────────


class _SubagentCwdMeta:
    """子 agent 的 cwd_meta 占位 (CwdMeta Protocol 形状).

    子 agent 与主进程同 cwd (实际路径由 shell 决定),
    不需要独立 cwd 语义。仅满足 Context.cwd_meta 的类型约束
    (runtime_checkable Protocol 按属性存在性判 isinstance)。
    """

    cwd_path: str = ""
    git_branch: str | None = None
    git_remote: str | None = None


class SpawnSubagentTool:
    """生成子 agent execute独立子task (§4.2, §9.3).

    用途:
      - 主 agent 把复杂子任务委托给子 agent
      - 子 agent 独立运行, 返回结果
      - 子 agent 的工具权限比主 agent 更严格

    参数:
      prompt: str          — 子任务的描述 (子 agent 的 user_raw)
      goal_type: str       — 子任务的 GoalType (默认 "investigate")
      subagent_type: str   — 子 agent 类型: "general-purpose" | "explore" | "plan" (默认 "general-purpose")
      write_access: bool   — 是否允许子 agent 写文件 (默认 False, 只读)

    IPR-0 不变量:
      - MAX_SUBAGENT_STEPS=10 (防 runaway)
      - 子 agent 禁止 spawn (防无限嵌套)
      - 子 agent 的 bash/write 默认走 GREYLIST (除非 write_access=True)
      - 子 agent 失败返回非空 error 消息 (不静默吞错)
    """

    __test__ = False

    MAX_SUBAGENT_STEPS: int = 10
    _MAX_PARALLEL: int = 5  # 并行子 agent 上限
    _SUBAGENT_OPTIONS = [
        "general-purpose",
        "explore",
        "plan",
    ]

    def __init__(
        self,
        model_provider: Any = None,
        tools: ToolRegistry | None = None,
        rules: RuleSet | None = None,
    ) -> None:
        # model_provider / tools / rules 在 CLI 层通过 set_context() inject
        # toolregister时这些可能尚未就绪
        self._model_provider = model_provider
        self._tools = tools
        self._rules = rules
        # Team Mode: thread池 + 子 agent trace
        self._executor = ThreadPoolExecutor(max_workers=self._MAX_PARALLEL)
        self._subagents: dict[str, dict[str, Any]] = {}
        self._subagents_lock = threading.Lock()
        # v2 fix: 标记是否已cleanup, 防 __del__ 和 close() 重复execute
        self._closed = False

    def set_context(self, model_provider: Any, tools: ToolRegistry, rules: RuleSet) -> None:
        """CLI 层在 model/tools/rules 就绪后injectcontext。"""
        self._model_provider = model_provider
        self._tools = tools
        self._rules = rules

    @property
    def tool_id(self) -> str:
        return "spawn_subagent"

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "spawn_subagent",
                "description": (
                    "Delegate an isolated sub-task to a sub-agent. "
                    "The sub-agent runs independently with its own tools and returns results. "
                    "By default, the sub-agent is read-only (cannot write files). "
                    "Use this for: parallel file analysis, independent investigation, "
                    "information gathering across multiple files. "
                    "For complex multi-step tasks, break the work into sub-tasks "
                    "and delegate each to a sub-agent via spawn_subagent. "
                    "Set parallel=true to run the sub-agent in background "
                    "(use the list_subagents tool to check status and get results later)."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "prompt": {
                            "type": "string",
                            "description": "Detailed description of the sub-task (the sub-agent's only input)",
                        },
                        "goal_type": {
                            "type": "string",
                            "enum": [gt.value for gt in GoalType],
                            "description": "Goal type for the sub-task (default: investigate)",
                            "default": "investigate",
                        },
                        "subagent_type": {
                            "type": "string",
                            "enum": self._SUBAGENT_OPTIONS,
                            "description": (
                                "Type of sub-agent to spawn: "
                                "'general-purpose' (read/write/execute, default), "
                                "'explore' (read-only codebase exploration), "
                                "'plan' (read-only planning and analysis)."
                            ),
                            "default": "general-purpose",
                        },
                        "write_access": {
                            "type": "boolean",
                            "description": "Allow the sub-agent to write files (default: false, read-only)",
                        },
                        "parallel": {
                            "type": "boolean",
                            "description": "Run in background (parallel mode). Default: false (synchronous). "
                                           "Use the list_subagents tool to check status and collect results.",
                            "default": False,
                        },
                    },
                    "required": ["prompt"],
                },
            },
        }

    def execute(self, args: dict[str, Any]) -> ToolResult:
        """execute子 agent 并return结果.

        Args:
            args: {"prompt": str, "goal_type"?: str,
                   "subagent_type"?: str, "write_access"?: bool, "parallel"?: bool}

        Returns:
            ToolResult with subagent output (synchronous) or tracking ID (parallel)
        """
        if self._model_provider is None or self._tools is None:
            return ToolResult(
                success=False,
                output="",
                error="spawn_subagent: context not initialized (call set_context first)",
            )

        prompt = args.get("prompt", "")
        if not prompt or not prompt.strip():
            return ToolResult(
                success=False,
                output="",
                error="spawn_subagent: prompt must be non-empty",
            )

        goal_type_str = args.get("goal_type", "investigate")
        try:
            goal_type = GoalType(goal_type_str)
        except ValueError:
            goal_type = GoalType.INVESTIGATE

        subagent_type = args.get("subagent_type", "general-purpose")
        if not isinstance(subagent_type, str) or subagent_type not in self._SUBAGENT_OPTIONS:
            subagent_type = "general-purpose"

        write_access = args.get("write_access", False)
        if not isinstance(write_access, bool):
            write_access = False

        parallel = args.get("parallel", False)
        if not isinstance(parallel, bool):
            parallel = False

        # construct子 agent 的 Goal, Context, Rules, Tools
        sub_goal, sub_context, sub_rules, sub_tools, sub_system_prompt = (
            self._build_subagent_env(prompt, goal_type, subagent_type, write_access)
        )

        if parallel:
            return self._execute_parallel(prompt, sub_goal, sub_context, sub_rules,
                                          sub_tools, sub_system_prompt)
        else:
            return self._execute_sync(prompt, sub_goal, sub_context, sub_rules,
                                      sub_tools, sub_system_prompt)

    def _build_subagent_env(self, prompt: str, goal_type: GoalType,
                            subagent_type: str, write_access: bool,
                            ) -> tuple[GoalTriple, Context, RuleSet, ToolRegistry, str]:
        """construct子 agent 运行环境 (Goal, Context, Rules, Tools, SystemPrompt).

        使用 AgentDefinition 根据 subagent_type 选择工具集和系统提示。
        """
        from zall.core.agent import (
            SubagentCapabilityMode,
            get_named_agent,
        )
        from zall.core.toolset import build_native_tools_for_preset, filter_tools_by_ids

        class _SubTermination:
            exposed_dependency_set: tuple[str, ...] | None = None
            def __call__(self, state: object) -> TerminationState:
                return TerminationState.UNDECIDABLE

        sub_goal = GoalTriple(
            statement=GoalStatement(
                intent=prompt.strip(),
                rewriting=prompt.strip(),
                rewrite_confidence=0.7,
                goal_type=goal_type,
                added_intent=(),
            ),
            termination=_SubTermination(),
            acceptance=AcceptanceContract(baseline_frozen_at="subagent_spawn"),
        )

        sub_context = Context(
            user_raw=prompt.strip(),
            cwd_meta=_SubagentCwdMeta(),
        )

        sub_rules = _build_subagent_rules(self._rules, write_access=write_access)

        # 根据 subagent_type 获取 AgentDefinition 并构建工具集
        agent_def = get_named_agent(subagent_type)
        if agent_def is not None:
            # 从 AgentDefinition 构建工具集
            sub_tools_list = build_native_tools_for_preset(agent_def.toolset.value)

            # 应用能力模式过滤
            cap_mode = agent_def.capability_mode
            if cap_mode is not None and cap_mode != SubagentCapabilityMode.FULL:
                # 读取预设工具 ID 列表, 再根据能力模式过滤
                from zall.core.toolset import get_tool_ids_for_preset
                tool_ids = get_tool_ids_for_preset(agent_def.toolset.value)
                from zall.core.agent import filter_tools_by_capability
                allowed_ids = filter_tools_by_capability(tool_ids, cap_mode)
                sub_tools_list = filter_tools_by_ids(sub_tools_list, allowed_ids)

            sub_tools = ToolRegistry(tools=tuple(sub_tools_list))

            # 使用 AgentDefinition 的提示
            if agent_def.prompt_body:
                sub_system_prompt = agent_def.prompt_body
            else:
                sub_system_prompt = _build_subagent_prompt(subagent_type, write_access)
        else:
            # 回退: 继承父 agent 工具集
            sub_tools = _build_subagent_tools(self._tools) if self._tools is not None else ToolRegistry(tools=())
            sub_system_prompt = _build_subagent_prompt(subagent_type, write_access)

        return sub_goal, sub_context, sub_rules, sub_tools, sub_system_prompt

    def _execute_sync(self, prompt: str, sub_goal: GoalTriple, sub_context: Context, sub_rules: RuleSet, sub_tools: ToolRegistry,
                      sub_system_prompt: str) -> ToolResult:
        """synchronousexecute子 agent (向后compatible, defaultpattern)."""
        return self._run_subagent_impl(prompt, sub_goal, sub_context, sub_rules,
                                       sub_tools, sub_system_prompt)

    def _execute_parallel(self, prompt: str, sub_goal: GoalTriple, sub_context: Context, sub_rules: RuleSet, sub_tools: ToolRegistry,
                          sub_system_prompt: str) -> ToolResult:
        """parallelexecute子 agent: commit到thread池, 立即returntrace ID."""
        sub_id = uuid.uuid4().hex[:12]

        # register到trace表
        with self._subagents_lock:
            self._subagents[sub_id] = {
                "status": "running",
                "prompt": prompt.strip()[:200],
                "goal_type": sub_goal.statement.goal_type.value,
                "result": None,
            }

        # 在后台thread中execute
        future = self._executor.submit(
            self._run_subagent_impl, prompt, sub_goal, sub_context,
            sub_rules, sub_tools, sub_system_prompt
        )

        # register完成回调
        def _on_done(f: Any) -> None:
            try:
                result = f.result()
                with self._subagents_lock:
                    # Guard against race with close() which clears _subagents
                    if sub_id in self._subagents:
                        self._subagents[sub_id]["status"] = "completed"
                        self._subagents[sub_id]["result"] = result
            except Exception as e:
                with self._subagents_lock:
                    if sub_id in self._subagents:
                        self._subagents[sub_id]["status"] = "failed"
                        self._subagents[sub_id]["result"] = ToolResult(
                            success=False, output="", error=str(e),
                        )

        future.add_done_callback(_on_done)

        return ToolResult(
            success=True,
            output=f"[Subagent {sub_id} started in background]\n"
                   f"  Prompt: {prompt.strip()[:200]}\n"
                   f"  Use 'list_subagents' to check status and collect results.",
            artifacts={
                "subagent_id": sub_id,
                "subagent_status": "running",
                "parallel": True,
            },
        )

    def _run_subagent_impl(self, prompt: str, sub_goal: GoalTriple, sub_context: Context, sub_rules: RuleSet,
                           sub_tools: ToolRegistry, sub_system_prompt: str) -> ToolResult:
        """Run sub-agent loop and build result (thread-safe)."""
        try:
            sub_loop = AgentLoop(
                model=self._model_provider,
                tools=sub_tools,
                rules=sub_rules,
                goal=sub_goal,
                context=sub_context,
                user_responder=_SubagentResponder(),
                judge=None,
                max_steps=self.MAX_SUBAGENT_STEPS,
            )

            egress = sub_loop.run(
                system_prompt=sub_system_prompt,
            )

            result_lines = [
                "[Subagent completed]",
                f"  Prompt: {prompt.strip()[:200]}",
                f"  Steps: {egress.step_count}",
                f"  Tool calls: {egress.total_tool_calls}",
                f"  Final state: {egress.final_state.value}",
            ]

            if egress.error:
                result_lines.append(f"  Error: {egress.error}")

            timeline_content = ""
            for ev in reversed(sub_loop.recorder.events):
                if ev.event_type.value == "model_call":
                    content = ev.payload.get("content", "")
                    if content and content.strip():
                        timeline_content = content.strip()
                        break

            if timeline_content:
                result_lines.append(f"  Output: {timeline_content[:2000]}")

            return ToolResult(
                success=egress.error is None,
                output="\n".join(result_lines),
                artifacts={
                    "subagent_steps": egress.step_count,
                    "subagent_tool_calls": egress.total_tool_calls,
                    "subagent_state": egress.final_state.value,
                    "output_text": timeline_content,
                },
            )

        except Exception as e:
            return ToolResult(
                success=False,
                output=f"[Subagent failed: {e}]",
                error=str(e),
            )

    # ── Team Mode: list_subagents tool ──

    @property
    def list_subagents_tool_id(self) -> str:
        return "list_subagents"

    @property
    def list_subagents_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "list_subagents",
                "description": (
                    "List all spawned sub-agents and their status. "
                    "Use this after spawn_subagent(parallel=true) to check "
                    "if background sub-agents have completed and get their results. "
                    "Returns a table of sub-agent IDs, statuses, and results."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            },
        }

    def execute_list_subagents(self, args: dict[str, Any]) -> ToolResult:
        """query所有子 agent state.

        P1 fix: 完整输出返回 (原版截断到 120 字符, 主 agent 无法获取并行子 agent 结果)。
        """
        with self._subagents_lock:
            if not self._subagents:
                return ToolResult(
                    success=True,
                    output="[No sub-agents running]",
                    artifacts={"subagents": []},
                )

            lines = ["[Sub-agents]"]
            artifacts_list = []
            for sub_id, info in self._subagents.items():
                status = info["status"]
                status_icon = {"running": "⏳", "completed": "✅", "failed": "❌"}.get(status, "❓")
                lines.append(f"  {status_icon} [{sub_id}] {info['prompt'][:60]}")
                lines.append(f"     Status: {status}")

                full_output = ""
                full_error = ""
                if status == "completed" and info["result"]:
                    result = info["result"]
                    full_output = result.output or ""
                    if full_output:
                        if len(full_output) > 2000:
                            lines.append(f"     Output: {full_output[:2000]}")
                            lines.append(f"     ... (truncated, {len(full_output)} total chars)")
                        else:
                            lines.append(f"     Output: {full_output}")
                elif status == "failed" and info["result"]:
                    full_error = info["result"].error or ""
                    lines.append(f"     Error: {full_error}")

                artifacts_list.append({
                    "id": sub_id,
                    "status": info["status"],
                    "prompt": info["prompt"],
                    "output": full_output,
                    "error": full_error,
                })

            # P2 fix: cleanup已完成的子 agent (preserve最近 50 条, 防 _subagents dict 无限增长)
            if len(self._subagents) > 50:
                completed_ids = [
                    sid for sid, info in self._subagents.items()
                    if info.get("status") in ("completed", "failed")
                ]
                for sid in completed_ids[:len(self._subagents) - 50]:
                    del self._subagents[sid]

            return ToolResult(
                success=True,
                output="\n".join(lines),
                artifacts={"subagents": artifacts_list},
            )

    def close(self) -> None:
        """cleanup thread pool (CLI 层在 shutdown 时调用).

        M2: 在锁内 shutdown, 避免与 _on_done 回调竞态; 清理 _subagents 防泄漏。
        """
        if self._closed:
            return
        self._closed = True
        with self._subagents_lock:
            self._executor.shutdown(wait=False, cancel_futures=True)
            self._subagents.clear()

    def __del__(self) -> None:
        """GC 时安全清理 thread pool, 防泄漏."""
        try:
            self.close()
        except Exception:
            pass
