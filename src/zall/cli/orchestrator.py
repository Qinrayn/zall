"""zall.cli.orchestrator — AgentLoop orchestration wiring (extracted from app.py O5).

Handles:
  - Adapter construction
  - Goal construction + refinement
  - Tool registry construction
  - MCP tool loading
  - REPL loop construction
  - Goal confirmation

Corresponds to:
  §3.2    GoalTriple construction
  §3.3    GoalRefiner (minimal)
  §4.2    ToolRegistry
  §4.2.1  context_judge rules
  §4.5    CliUserResponder
  §5.2    Judge
  §6.1    RunRecorder + observer
  §9.2.11 MCP tools
"""

from __future__ import annotations

import sys
from typing import Any

from zall._util.logging import get_zall_logger as _get_zall_logger
from zall.cli import config as _cli_config
from zall.cli.environment import CwdMeta, build_system_prompt
from zall.cli.judge import SystemJudge, UndecidableJudge
from zall.cli.render import CliRenderer, clear_console_cache, render_goal_card
from zall.cli.responder import CliUserResponder
from zall.core.checkpoint import CheckpointManager
from zall.core.compactor import ModelCompactor
from zall.core.context import Context
from zall.core.goal import (
    AcceptanceContract,
    GoalStatement,
    GoalTriple,
    GoalType,
    RefinedGoal,
    TerminationState,
)
from zall.core.loop_events import RunEgress
from zall.core.plugin_loader import discover_tools, merge_tools_with_builtins
from zall.core.refiner import GoalRefiner
from zall.core.tool import ToolRegistry, ToolResult
from zall.mcp.config import MCPServerSpec, load_mcp_config
from zall.mcp.tool import MCPTool
from zall.safety.rules_file import load_rules
from zall.tools.apply_patch import ApplyPatchTool
from zall.tools.bash import BashTool
from zall.tools.batch_edit import BatchEditTool
from zall.tools.edit_file import EditFileTool
from zall.tools.git_protect import GitProtect
from zall.tools.glob import GlobTool
from zall.tools.grep import GrepTool
from zall.tools.list_dir import ListDirTool
from zall.tools.read_file import ReadFileTool
from zall.tools.read_image import ReadImageTool
from zall.tools.science import ScienceTool
from zall.tools.search import SearchTool
from zall.tools.spawn_subagent import SpawnSubagentTool
from zall.tools.todo import TodoListTool
from zall.tools.web_fetch import WebFetchTool
from zall.tools.write_file import WriteFileTool

_log = _get_zall_logger(__name__)


# ──────────────────────────────────────────────────────────────────────────
# Perception Engine 构造 (v0.6.0, MASTER.md §4.2.3)
# ──────────────────────────────────────────────────────────────────────────


def build_perception_engine(project_root: str | None = None) -> Any:
    """构造默认 Perception Engine (含 coding agent 传感器)。

    创建带 FileSensor + GitSensor 的 PerceptionEngine,
    注入 CodingWorldModel 用于轻量级预测。

    Returns:
        PerceptionEngine 实例 (或 None 如果不可用)
    """
    try:
        from zall.core.perception import PerceptionEngine
        from zall.core.perception.coding_sensors import FileSensor, GitSensor
        from zall.core.perception.coding_world_model import CodingWorldModel

        engine = PerceptionEngine()
        engine.add_sensor(FileSensor(project_root=project_root))
        engine.add_sensor(GitSensor(project_root=project_root))
        engine.set_world_model(CodingWorldModel(project_root=project_root))
        return engine
    except Exception:
        return None


# ──────────────────────────────────────────────────────────────────────────
# Adapter construct
# ──────────────────────────────────────────────────────────────────────────


def build_adapter(provider: str, model: str | None = None) -> Any:
    """construct adapter (委托给 cli.config._build_adapter, 统一patch点)。

    O9: 从配置加载 timeout 并传递给 adapter。
    环境变量 ZALL_TIMEOUT 优先级最高，可覆盖 config.toml。
    """
    import os as _os

    from zall.safety.config import load_config as _load_cfg
    cfg = _load_cfg()
    timeout = float(_os.environ.get("ZALL_TIMEOUT") or cfg.get("timeout", 300.0))
    return _cli_config._build_adapter(provider, model=model, timeout=timeout)


# ──────────────────────────────────────────────────────────────────────────
# GoalTriple construct
# ──────────────────────────────────────────────────────────────────────────


def _make_goal(user_task: str, *, judge_mode: str) -> GoalTriple:
    """无 Refiner 时的最小诚实 GoalTriple construct。"""
    if judge_mode == "system":
        goal_type = GoalType.BUGFIX
        exposed: tuple[str, ...] | None = ()
    else:
        goal_type = GoalType.UNKNOWN
        exposed = None

    from zall.core.refiner import _PlaceholderTermination as _Term

    return GoalTriple(
        statement=GoalStatement(
            intent=user_task,
            rewriting=user_task,
            rewrite_confidence=1.0,
            goal_type=goal_type,
            translation_of=(user_task,),
            added_intent=(),
        ),
        termination=_Term(exposed),
        acceptance=AcceptanceContract(baseline_frozen_at="cli_run"),
    )


def refine_goal(user_task: str, *, judge_mode: str) -> GoalTriple:
    """经 GoalRefiner (§3.3 minimal) construct GoalTriple, 带 fallback。"""
    try:
        refined: RefinedGoal = GoalRefiner.refine(user_task, judge_mode=judge_mode)
        return refined.refined_goal
    except Exception:
        return _make_goal(user_task, judge_mode=judge_mode)


def confirm_goal(
    out: Any, goal: Any, *, judge_mode: str, yes: bool, strict: bool = False,
    input_fn: Any = None,
) -> tuple[bool, Any]:
    """§9.2.1/§9.2.5 Goal lockconfirm: 开工前让用户"confirm承诺"。

    v0.5.1: 默认不交互。仅在 strict 模式或 judge_mode != "none" 时询问用户。
    始终渲染 goal card (信息性), 默认返回 True (auto-confirm)。

    v0.6.0 (UX): 在 goal card 下方显示提示, 允许用户直接输入新目标来修改。
    用户按 Enter 确认, 输入非空新目标则重新 refine 并循环。

    返回 (True, final_goal) = 用户确认; (False, None) = 拒绝, 调用方应中止。
    """
    current_goal = goal
    render_goal_card(current_goal, judge_mode, out)
    if yes:
        return True, current_goal
    if not sys.stdin.isatty():
        return True, current_goal
    # v0.5.1: 默认 auto-confirm, 仅在 strict 或 judge 模式时交互
    if not strict and judge_mode == "none":
        return True, current_goal
    ask = input_fn or input
    try:
        # Phase 2 (UX): 显示提示, 允许用户直接编辑目标
        out.write("  \u2500" * 25 + "\n")
        out.write("  Press Enter to confirm, or enter a new goal to modify\n")
        out.flush()
        while True:
            ans = ask("  goal > ").strip().lower()
            if not ans or ans in ("y", "yes"):
                # 确认当前 goal (接受 y/yes 作为确认, 向后兼容)
                return True, current_goal
            if ans in ("n", "no"):
                # 用户拒绝
                return False, None
            # 用户输入了新目标, 重新 refine 并循环
            current_goal = refine_goal(ans, judge_mode=judge_mode)
            render_goal_card(current_goal, judge_mode, out)
            out.write("  Press Enter to confirm, or enter a new goal to modify\n")
            out.flush()
    except (EOFError, KeyboardInterrupt):
        return False, None


# ──────────────────────────────────────────────────────────────────────────
# toolregister
# ──────────────────────────────────────────────────────────────────────────

# Item A: 惰性construct — tool在首次 build_tools() 调用时instance化,
# 不在模块导入时创建 (SpawnSubagentTool.__init__ 会启动 5 thread池)。
_NATIVE_TOOLS_CACHE: tuple[Any, ...] | None = None


def clear_native_tools_cache() -> None:
    """清除 native tools 缓存 (供测试隔离用)。

    O9: 同时清除 _LIST_SUBAGENTS_TOOL, 避免测试间实例泄漏。
    """
    global _NATIVE_TOOLS_CACHE, _LIST_SUBAGENTS_TOOL
    _NATIVE_TOOLS_CACHE = None
    _LIST_SUBAGENTS_TOOL = None


def _get_native_tools() -> tuple[Any, ...]:
    """惰性construct并cache 14 个核心tool (Item A)。"""
    global _NATIVE_TOOLS_CACHE
    if _NATIVE_TOOLS_CACHE is None:
        _NATIVE_TOOLS_CACHE = (
            ReadFileTool(),
            WriteFileTool(),
            EditFileTool(),
            ApplyPatchTool(),
            BatchEditTool(),
            BashTool(),
            GrepTool(),
            GlobTool(),
            ListDirTool(),
            WebFetchTool(),
            SearchTool(),
            ReadImageTool(),
            SpawnSubagentTool(),
            TodoListTool(),
            ScienceTool(),
        )
    return _NATIVE_TOOLS_CACHE


# ── v0.3.0: 工具集预设支持 ──


def build_tools_for_preset(preset: str) -> ToolRegistry:
    """按工具集预设构建 ToolRegistry。

    Args:
        preset: 预设名称 (zall / explore / plan / codex / opencode)

    Returns:
        ToolRegistry with the preset's tools + list_subagents
    """
    from zall.core.toolset import build_native_tools_for_preset

    tools = build_native_tools_for_preset(preset)

    # 添加 list_subagents (如果包含 spawn_subagent)
    spawn = next(
        (t for t in tools if t.tool_id == "spawn_subagent"),
        None,
    )
    if spawn is not None:
        tools.append(_ListSubagentsTool(spawn))

    return ToolRegistry(tools=tuple(tools))


class _ListSubagentsTool:
    """Team Mode: query子 agent state (委托给 SpawnSubagentTool)."""

    __test__ = False

    def __init__(self, spawn_tool: SpawnSubagentTool) -> None:
        self._spawn = spawn_tool

    @property
    def tool_id(self) -> str:
        return "list_subagents"

    @property
    def schema(self) -> dict[str, Any]:
        return self._spawn.list_subagents_schema

    def execute(self, args: dict[str, Any]) -> ToolResult:
        return self._spawn.execute_list_subagents(args)


# B7 fix: 惰性construct _ListSubagentsTool, 避免导入时崩溃
# _SPAWN_TOOL 在 build_tools() 首次调用时find, 不在导入时execute
_LIST_SUBAGENTS_TOOL: _ListSubagentsTool | None = None


def build_tools() -> ToolRegistry:
    """register全部核心tool (§4.2 tool层), 含 list_subagents (Team Mode).

    同时通过 entry-point 发现机制加载第三方插件工具 (MASTER.md §12.1).
    内置工具优先: 同名 tool_id 的插件工具被丢弃。
    """
    global _LIST_SUBAGENTS_TOOL
    native = _get_native_tools()
    if _LIST_SUBAGENTS_TOOL is None:
        spawn = next((t for t in native if t.tool_id == "spawn_subagent"), None)
        if spawn is None:
            raise RuntimeError("spawn_subagent tool not found in native tools")
        _LIST_SUBAGENTS_TOOL = _ListSubagentsTool(spawn)
    all_tools = native + (_LIST_SUBAGENTS_TOOL,)
    # 发现并合并插件工具 (内置优先)
    plugin_tools = discover_tools()
    all_tools = merge_tools_with_builtins(plugin_tools, all_tools)
    return ToolRegistry(tools=all_tools)


def merge_tools(native: tuple[Any, ...], mcp_tools: list[MCPTool]) -> ToolRegistry:
    """merge native tool与 MCP tool, return新 ToolRegistry。"""
    return ToolRegistry(tools=tuple(native) + tuple(mcp_tools))


def build_mcp_tools(
    out_stream: Any = None, *, servers: list[MCPServerSpec] | None = None
) -> list[MCPTool]:
    """load并连接 MCP server, 把每个暴露的 tool 包成 MCPTool (§9.2.11)。

    失败安全 (IPR-0): 任一 server 连接失败 → 跳过, 不影响其余。
    B3 fix: 使用 client=None guard 确保 list_tools() 失败时也关闭 client。
    """
    from zall.mcp.client import MCPClient

    out = out_stream or sys.stderr
    if servers is None:
        servers = load_mcp_config()
    tools: list[MCPTool] = []
    for spec in servers:
        client = None
        try:
            client = MCPClient(
                command=spec.command,
                args=list(spec.args),
                env=dict(spec.env) or None,
            )
            client.connect()  # 拆分为两步: 构造 + 连接 (fix B4)
            tool_specs = client.list_tools()
        except Exception as e:
            out.write(f"  [mcp] skip server '{spec.name}': {e}\n")
            out.flush()
            if client is not None:
                try:
                    client.close()
                except Exception:
                    pass
            continue
        if not tool_specs:
            try:
                client.close()
            except Exception:
                pass
            continue
        for ts in tool_specs:
            tools.append(MCPTool(server_name=spec.name, spec=ts, client=client))
    return tools


def inject_subagent_context(tools: ToolRegistry, model: Any, rules: Any) -> None:
    """将 model + tools + rules inject SpawnSubagentTool。"""
    spawn = tools.get("spawn_subagent")
    if spawn is not None and hasattr(spawn, "set_context"):
        spawn.set_context(model, tools, rules)


# ──────────────────────────────────────────────────────────────────────────
# Observer construct
# ──────────────────────────────────────────────────────────────────────────


def make_usage_observer(inner: Any, state: dict[str, Any]) -> Any:
    """包装 observer: 累计 token usage 到 state["usage"]。"""

    def _obs(event: Any) -> None:
        if event.kind == "model_call":
            usage = event.payload.get("usage") or {}
            if usage:
                u = state.setdefault("usage", {"prompt": 0, "completion": 0})
                u["prompt"] += int(usage.get("prompt", 0) or 0)
                u["completion"] += int(usage.get("completion", 0) or 0)
                # 上下文占用 (bottom toolbar 用): 最近一次调用的 input tokens = 当前 context 大小
                _pt = int(usage.get("prompt", 0) or 0)
                if _pt:
                    state["ctx_tokens"] = _pt
        inner(event)

    return _obs


# ──────────────────────────────────────────────────────────────────────────
# REPL 循环construct
# ──────────────────────────────────────────────────────────────────────────


def get_modified_files(baseline: frozenset[str] | None = None) -> list[str] | None:
    """获取本次 run 期间新产生的修改文件列表。

    Bug fix (2026-07-26): 无基线时 `git diff HEAD` 拿到的是工作区**全部**
    未提交改动 — 脏工作区下纯 Q&A 会话也会误报 "modified: 167 file(s)"。
    正确语义: run 前采 baseline 集合, 结束后只报差集 (本次新增的改动)。
    baseline=None 保留旧行为 (兼容直接调用方)。
    """
    import subprocess
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", "HEAD"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=5,
        )
        if result.returncode != 0:
            return None
        files = [f.strip() for f in result.stdout.split("\n") if f.strip()]
        if baseline is not None:
            files = [f for f in files if f not in baseline]
        return files if files else None
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return None


def snapshot_modified_baseline() -> frozenset[str]:
    """run 启动前采集当前工作区已有改动集合 (get_modified_files 的基线)。"""
    import subprocess
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", "HEAD"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=5,
        )
        if result.returncode != 0:
            return frozenset()
        return frozenset(f.strip() for f in result.stdout.split("\n") if f.strip())
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return frozenset()


def run(
    user_task: str,
    *,
    model: str | None = None,
    yes: bool = False,
    judge_mode: str = "none",
    json_mode: bool = False,
    max_steps: int | None = None,
    stream: bool = True,
    verbose: bool = False,
    out: Any = None,
    enable_repo_map: bool = True,
    agent_definition: Any = None,
    toolset_preset: str | None = None,
    strict: bool = False,
) -> RunEgress:
    """接线 AgentLoop 并execute (薄接线层, 不重新编排primitive)。

    stream: True 且 adapter 支持 complete_stream → token 级流式显示
    out: 输出流 (默认 sys.stderr); REPL/测试可注入
    agent_definition: 可选 AgentDefinition, 用于覆盖工具集/权限模式
    toolset_preset: 可选工具集预设名 (覆盖 AgentDefinition 和默认工具集)
    strict: 严格模式 (v0.5.1), 启用 full confirm/downgrade gates
    """
    out_stream = out or sys.stderr

    # v2.x: @file 引用展开 — 与 REPL/TUI 一致 (三路径齐平)。一次性/JSON 模式**静默**展开
    # (不打印提示以免污染 NDJSON); @path 解析到真实文件则注入内容, 非文件原样保留。
    from zall.cli.file_complete import expand_at_references
    user_task, _ = expand_at_references(user_task)

    # 1. adapter
    try:
        provider = _cli_config._detect_provider(model)
        adapter = build_adapter(provider, model=model)
    except Exception as e:  # B4 fix: 拓宽异常捕获范围
        out_stream.write(f"  ✗ config error: {e}\n")
        out_stream.write('  hint: set api_key in ~/.zall/config.toml or ZALL_API_KEY env\n')
        return RunEgress(
            run_id="no_run", final_state=TerminationState.UNDECIDABLE,
            step_count=0, total_tool_calls=0, total_model_calls=0, error=str(e),
        )

    # 2. tools — 支持 toolset 预设
    if toolset_preset is not None:
        # 使用预设构建工具集
        native_tools = build_tools_for_preset(toolset_preset)
    elif agent_definition is not None:
        # 从 AgentDefinition 构建工具集
        from zall.core.toolset import build_native_tools_for_preset
        tool_list = build_native_tools_for_preset(agent_definition.toolset.value)
        # 过滤 disallowed_tools
        if agent_definition.disallowed_tools:
            tool_list = [t for t in tool_list if t.tool_id not in agent_definition.disallowed_tools]
        # 过滤 tools allowlist
        if agent_definition.tools:
            tool_list = [t for t in tool_list if t.tool_id in agent_definition.tools]
        native_tools = ToolRegistry(tools=tuple(tool_list))
    else:
        native_tools = build_tools()  # build_tools() 已包含插件发现

    # 插件工具发现 (仅 preset / agent_definition 路径, 默认路径已在 build_tools() 中)
    if toolset_preset is not None or agent_definition is not None:
        plugin_tools = discover_tools()
        merged = merge_tools_with_builtins(plugin_tools, native_tools.tools)
        native_tools = ToolRegistry(tools=merged)

    mcp_tools = build_mcp_tools(out_stream)
    tools = merge_tools(native_tools.tools, mcp_tools)

    # 3. rules
    rules = load_rules()
    inject_subagent_context(tools, adapter, rules)

    # 4. goal
    goal = refine_goal(user_task, judge_mode=judge_mode)

    # 4.5 Goal confirmation
    confirmed, final_goal = confirm_goal(
        out_stream, goal, judge_mode=judge_mode, yes=yes, strict=strict,
    )
    if not confirmed:
        out_stream.write("  goal not confirmed by user; aborting.\n")
        out_stream.flush()
        return RunEgress(
            run_id="no_run", final_state=TerminationState.UNDECIDABLE,
            step_count=0, total_tool_calls=0, total_model_calls=0,
            error="goal not confirmed by user",
        )
    goal = final_goal  # 使用可能被用户修改的 goal

    # 5. context
    # P2 note: run() 是一次性execute, 无持久 state dict 可传给 get_cached_cwd_meta。
    # REPL 中用 get_cached_cwd_meta(state) 避免每 prompt 都 spawn git 子process;
    # run() 只construct一次 CwdMeta, 直接instance化即可, cache无收益。
    context = Context(user_raw=user_task, cwd_meta=CwdMeta())

    # 6. responder
    is_interactive = out is None and sys.stdin.isatty()
    _resp_stream = out_stream
    def _print_fn(s: str) -> None:
        _resp_stream.write(s + "\n")
        _resp_stream.flush()
    def _greylist_choose(choices: list[tuple[str, str, str]]) -> str:
        from zall.cli.select import select_prompt
        return select_prompt(_resp_stream, "confirm tool call", choices, default_index=1)
    responder = CliUserResponder(
        yes=yes, is_tty=is_interactive,
        print_fn=_print_fn,
        choose_fn=_greylist_choose,
    )

    # 7. judge
    if judge_mode == "system":
        judge: Any = SystemJudge()
    else:
        judge = UndecidableJudge()

    # 8. renderer + observer
    renderer = CliRenderer(json_mode=json_mode, stream=out_stream, verbose=verbose,
                           disable_spinner=stream)
    usage_state: dict[str, Any] = {"usage": {"prompt": 0, "completion": 0}}
    observer = make_usage_observer(renderer, usage_state)

    # 9. safety nets
    git_protect = GitProtect()
    try:
        checkpoint_mgr = CheckpointManager()
    except (OSError, PermissionError, ValueError) as _cp_err:
        _log.warning("checkpoint manager unavailable: %s", _cp_err)
        checkpoint_mgr = None

    # 10. AgentLoop (via AgentBuilder — O9: unified construction path)
    from zall.core.builder import AgentBuilder
    loop = (
        AgentBuilder()
        .with_model(adapter)
        .with_tools(tools)
        .with_rules(rules)
        .with_goal(goal)
        .with_context(context)
        .with_responder(responder)
        .with_judge(judge)
        .with_observer(observer)
        .with_max_steps(max_steps)
        .with_stream(stream)
        .with_git_protect(git_protect)
        .with_checkpoint(checkpoint_mgr)
        .with_compactor(ModelCompactor())
        .with_strict(strict)
        .build()
    )

    # 11. Execute
    out_stream.write(f"  {user_task[:100]}\n")
    out_stream.flush()

    # 修改文件报告基线: 只报本次 run 新产生的改动, 不报工作区存量脏改动
    modified_baseline = snapshot_modified_baseline()

    try:
        egress = loop.run(system_prompt=build_system_prompt(
            context, mcp_tools=tuple(mcp_tools),
            enable_repo_map=enable_repo_map,
            lean=(toolset_preset == "lean"),  # PARADIGM Step 0: lean 预设→极简提示
        ))
    finally:
        for t in mcp_tools:
            t.close()
        # O1: 关闭 adapter HTTP 客户端 (httpx 连接池leak防护)
        if hasattr(adapter, "close"):
            try:
                adapter.close()
            except Exception as _close_err:
                _log.warning("adapter close failed (non-fatal): %s", _close_err)
        clear_console_cache()  # v0.3.0 (A2): 释放累积的 Console 缓存

    # 12. TrustAnchor + save
    try:
        from zall.core.verifiability import FileTrustAnchor
        trust_anchor = FileTrustAnchor()
    except Exception as _ta_err:
        _log.warning("trust anchor unavailable: %s", _ta_err)
        trust_anchor = None

    from zall.cli.session import _save_session
    session_dir = None
    try:
        session_dir = _save_session(loop.run_id, loop, egress, anchor=trust_anchor)
    except Exception as _save_err:
        # 持久化失败不应掩盖task成功 (Bug: session save崩溃致每次task exit 1)
        import sys as _sys
        print(f"  ⚠ session save failed (non-fatal): {_save_err}", file=_sys.stderr)

    modified_files = get_modified_files(modified_baseline)

    from zall.cli.render import render_egress_summary
    render_egress_summary(
        run_id=loop.run_id,
        final_state=egress.final_state.value,
        step_count=egress.step_count,
        tool_calls=egress.total_tool_calls,
        model_calls=egress.total_model_calls,
        error=egress.error,
        session_dir=str(session_dir),
        stream=out_stream,
        usage=usage_state["usage"],
        modified_files=modified_files,
        judge_ran=(judge_mode != "none"),
    )

    # PARADIGM Step 1: 将本次任务写入持久经验流 (跨会话复利)。
    # Popperian Gate: final_state==MET (过了 judge) → verified 技能; 否则仅历史。
    # IPR-0: 记录失败不影响任务结果。
    try:
        from zall.core.experience_store import get_experience_store
        from zall.core.goal import TerminationState as _TS
        _verified = egress.final_state == _TS.MET
        _outcome = (egress.final_claim or "").strip()
        try:
            for _m in reversed(list(getattr(loop, "messages", []) or [])):
                if getattr(_m, "role", "") == "assistant" and getattr(_m, "content", ""):
                    _outcome = str(_m.content).strip()[:1000]
                    break
        except Exception:
            pass
        get_experience_store().record(
            user_task, _outcome, verified=_verified, score=1.0 if _verified else 0.5,
        )
    except Exception as _exp_err:
        _log.debug("experience record skipped (non-fatal): %s", _exp_err)

    return egress