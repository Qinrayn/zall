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
from zall.cli.judge import SystemJudge, UndecidableJudge
from zall.cli.render import CliRenderer, render_goal_card, clear_console_cache
from zall.cli.responder import CliUserResponder
from zall.cli.environment import build_system_prompt, CwdMeta
from zall.core.context import Context
from zall.core.goal import (
    AcceptanceContract,
    GoalStatement,
    GoalTriple,
    GoalType,
    RefinedGoal,
    TerminationState,
)
from zall.core.loop import RunEgress
from zall.core.refiner import GoalRefiner
from zall.core.tool import ToolRegistry, ToolResult
from zall.core.compactor import ModelCompactor
from zall.core.checkpoint import CheckpointManager
from zall.safety.rules_file import load_rules
from zall.tools.bash import BashTool
from zall.tools.batch_edit import BatchEditTool
from zall.tools.edit_file import EditFileTool
from zall.tools.git_protect import GitProtect
from zall.tools.glob import GlobTool
from zall.tools.grep import GrepTool
from zall.tools.list_dir import ListDirTool
from zall.tools.read_file import ReadFileTool
from zall.tools.spawn_subagent import SpawnSubagentTool
from zall.tools.write_file import WriteFileTool
from zall.tools.todo import TodoListTool
from zall.tools.web_fetch import WebFetchTool
from zall.tools.read_image import ReadImageTool
from zall.tools.search import SearchTool
from zall.mcp.config import load_mcp_config, MCPServerSpec
from zall.mcp.tool import MCPTool

_log = _get_zall_logger(__name__)


# ──────────────────────────────────────────────────────────────────────────
# Adapter construct
# ──────────────────────────────────────────────────────────────────────────


def build_adapter(provider: str, model: str | None = None) -> Any:
    """construct adapter (委托给 cli.config._build_adapter, 统一patch点)。

    O9: 从配置加载 timeout 并传递给 adapter。
    """
    from zall.safety.config import load_config as _load_cfg
    cfg = _load_cfg()
    timeout = cfg.get("timeout", 120.0)
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
    out: Any, goal: Any, *, judge_mode: str, yes: bool, input_fn: Any = None
) -> bool:
    """§9.2.1/§9.2.5 Goal lockconfirm: 开工前让用户"confirm承诺"。

    返回 True = 用户确认; False = 拒绝, 调用方应中止。
    """
    render_goal_card(goal, judge_mode, out)
    if yes:
        return True
    if not sys.stdin.isatty():
        return True
    ask = input_fn or input
    try:
        ans = ask("  confirm goal? [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    return ans in ("y", "yes")


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
    """惰性construct并cache 13 个核心tool (Item A)。"""
    global _NATIVE_TOOLS_CACHE
    if _NATIVE_TOOLS_CACHE is None:
        _NATIVE_TOOLS_CACHE = (
            ReadFileTool(),
            WriteFileTool(),
            EditFileTool(),
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
    """register全部核心tool (§4.2 tool层), 含 list_subagents (Team Mode)."""
    global _LIST_SUBAGENTS_TOOL
    native = _get_native_tools()
    if _LIST_SUBAGENTS_TOOL is None:
        spawn = next((t for t in native if t.tool_id == "spawn_subagent"), None)
        if spawn is None:
            raise RuntimeError("spawn_subagent tool not found in native tools")
        _LIST_SUBAGENTS_TOOL = _ListSubagentsTool(spawn)
    return ToolRegistry(tools=native + (_LIST_SUBAGENTS_TOOL,))


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
        inner(event)

    return _obs


# ──────────────────────────────────────────────────────────────────────────
# REPL 循环construct
# ──────────────────────────────────────────────────────────────────────────


def get_modified_files() -> list[str] | None:
    """通过 git diff --name-only 获取当前工作区被修改的filelist。"""
    import subprocess
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            return None
        files = [f.strip() for f in result.stdout.split("\n") if f.strip()]
        return files if files else None
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return None


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
) -> RunEgress:
    """接线 AgentLoop 并execute (薄接线层, 不重新编排primitive)。

    stream: True 且 adapter 支持 complete_stream → token 级流式显示
    out: 输出流 (默认 sys.stderr); REPL/测试可注入
    agent_definition: 可选 AgentDefinition, 用于覆盖工具集/权限模式
    toolset_preset: 可选工具集预设名 (覆盖 AgentDefinition 和默认工具集)
    """
    out_stream = out or sys.stderr

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
        native_tools = build_tools()
    mcp_tools = build_mcp_tools(out_stream)
    tools = merge_tools(native_tools.tools, mcp_tools)

    # 3. rules
    rules = load_rules()
    inject_subagent_context(tools, adapter, rules)

    # 4. goal
    goal = refine_goal(user_task, judge_mode=judge_mode)

    # 4.5 Goal confirmation
    if not confirm_goal(out_stream, goal, judge_mode=judge_mode, yes=yes):
        out_stream.write("  goal not confirmed by user; aborting.\n")
        out_stream.flush()
        return RunEgress(
            run_id="no_run", final_state=TerminationState.UNDECIDABLE,
            step_count=0, total_tool_calls=0, total_model_calls=0,
            error="goal not confirmed by user",
        )

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
    responder = CliUserResponder(
        yes=yes, is_tty=is_interactive,
        print_fn=_print_fn,
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
        .build()
    )

    # 11. Execute
    out_stream.write(f"  {user_task[:100]}\n")
    out_stream.flush()

    try:
        egress = loop.run(system_prompt=build_system_prompt(
            context, mcp_tools=tuple(mcp_tools),
            enable_repo_map=enable_repo_map,
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

    modified_files = get_modified_files()

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
    )
    return egress