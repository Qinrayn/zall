"""zall.core.agent — Agent 定义 + 工具集预设 + 子 agent 能力模式。

对应 Grok Build 的 xai-grok-agent crate:
  - AgentDefinition: YAML frontmatter agent 文件定义
  - ToolsetPreset: 工具集预设 (explore/plan/codex/opencode/zall)
  - SubagentCapabilityMode: 子 agent 能力限制过滤
  - BuiltinAgent: 内置 agent 枚举

IPR constraints:
  IPR-0: invariant tests at tests/test_agent_definition.py
  IPR-1: corresponds to DESIGN.md §4.2 + §9.2.10 + §9.2.11
  IPR-3: pydantic / stdlib only, no model SDK
"""

from __future__ import annotations

import os
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict


# ═══════════════════════════════════════════════════════════════════
# §1  ToolsetPreset — 工具集预设
# ═══════════════════════════════════════════════════════════════════


class ToolsetPreset(str, Enum):
    """工具集预设 — 决定 agent 拥有哪些工具 (参考 Grok Build 的 toolset presets)。

    Each preset maps to a specific tool roster:
      ZALL      — 全功能: bash + read + write + edit + grep + subagent + web + …
      EXPLORE   — 只读探索: read_file + grep + list_dir + search **only**
      PLAN      — 规划: read_file + grep + list_dir + search + todo **only**
      CODEX     — Codex 兼容: bash + read + apply_patch + grep
      OPENCODE  — OpenCode 兼容: bash + read + write + edit + grep + skill
    """

    ZALL = "zall"
    EXPLORE = "explore"
    PLAN = "plan"
    CODEX = "codex"
    OPENCODE = "opencode"


# ═══════════════════════════════════════════════════════════════════
# §2  SubagentCapabilityMode — 子 agent 能力限制
# ═══════════════════════════════════════════════════════════════════


class SubagentCapabilityMode(str, Enum):
    """子 agent 能力模式 — 限制子 agent 可用的工具种类。

    从 Grok Build 的 `xai_tool_types::SubagentCapabilityMode` 借鉴。
    在 spawn 时通过过滤 AgentDefinition 的 tool_config 来实施。

    模式层次 (由紧到松):
      READ_ONLY  — 只读 (禁止所有写操作)
      PLAN_ONLY  — 规划 (只读 + todo, 禁止 shell/写)
      NO_BASH    — 禁止 shell (允许写文件但不允许 bash)
      FULL       — 无限制 (继承父 agent 的完整工具集)
    """

    FULL = "full"
    READ_ONLY = "read_only"
    PLAN_ONLY = "plan_only"
    NO_BASH = "no_bash"


# 每种模式封锁的工具 ID 列表
_CAPABILITY_BLOCKLIST: dict[SubagentCapabilityMode, frozenset[str]] = {
    SubagentCapabilityMode.READ_ONLY: frozenset({
        "bash", "write_file", "edit_file", "batch_edit",
    }),
    SubagentCapabilityMode.PLAN_ONLY: frozenset({
        "bash", "write_file", "edit_file", "batch_edit",
    }),
    SubagentCapabilityMode.NO_BASH: frozenset({
        "bash",
    }),
    SubagentCapabilityMode.FULL: frozenset(),
}


def filter_tools_by_capability(
    tool_ids: list[str],
    mode: SubagentCapabilityMode,
) -> list[str]:
    """按能力模式过滤工具 ID 列表。

    Args:
        tool_ids: 原始工具 ID 列表
        mode: 能力模式

    Returns:
        过滤后的工具 ID 列表 (只减不增)
    """
    blocklist = _CAPABILITY_BLOCKLIST.get(mode, frozenset())
    if not blocklist:
        return list(tool_ids)
    return [tid for tid in tool_ids if tid not in blocklist]


# ═══════════════════════════════════════════════════════════════════
# §3  PermissionMode — 权限模式
# ═══════════════════════════════════════════════════════════════════


class PermissionMode(str, Enum):
    """权限模式 — 控制 agent 的工具确认策略。

    借鉴 Grok Build 的 PermissionMode:
      DEFAULT  — 默认 (greylist 询问用户, blacklist 拒绝)
      PLAN     — 规划模式 (write 操作自动拒绝)
      BYPASS   — 绕过权限 (自动接受所有, 用于子 agent)
    """

    DEFAULT = "default"
    PLAN = "plan"
    BYPASS = "bypassPermissions"


# ═══════════════════════════════════════════════════════════════════
# §4  AgentScope — agent 定义来源
# ═══════════════════════════════════════════════════════════════════


class AgentScope(str, Enum):
    """Agent 定义发现范围 — 决定优先级。

    Project > User > Bundled > BuiltIn
    """
    PROJECT = "project"
    USER = "user"
    BUNDLED = "bundled"
    BUILTIN = "built-in"


# ═══════════════════════════════════════════════════════════════════
# §5  AgentDefinition — agent 定义
# ═══════════════════════════════════════════════════════════════════

# 默认的 zall agent 目录
ZALL_AGENTS_DIR = ".zall/agents"


# ── camelCase -> snake_case 转换 ──
# YAML frontmatter 使用 camelCase, Pydantic 使用 snake_case。
# 这个查找表做双向映射。

_CAMEL_TO_SNAKE: dict[str, str] = {
    "name": "name",
    "description": "description",
    "toolset": "toolset",
    "permissionMode": "permission_mode",
    "permission_mode": "permission_mode",
    "capabilityMode": "capability_mode",
    "capability_mode": "capability_mode",
    "model": "model",
    "skills": "skills",
    "discoverSkills": "discover_skills",
    "discover_skills": "discover_skills",
    "mcpServers": "mcp_servers",
    "mcp_servers": "mcp_servers",
    "mcpInheritance": "mcp_inheritance",
    "mcp_inheritance": "mcp_inheritance",
    "disallowedTools": "disallowed_tools",
    "disallowed_tools": "disallowed_tools",
    "tools": "tools",
    "allowedSubagentTypes": "allowed_subagent_types",
    "allowed_subagent_types": "allowed_subagent_types",
    "sourcePath": "source_path",
    "source_path": "source_path",
    "promptBody": "prompt_body",
    "prompt_body": "prompt_body",
    "scope": "scope",
}


def _normalize_keys(data: dict) -> dict:
    """将 YAML/JSON 的 camelCase 键转换为 snake_case。"""
    return {_CAMEL_TO_SNAKE.get(k, k): v for k, v in data.items()}


class AgentDefinition(BaseModel):
    """可移植的 agent 身份 — 从 .zall/agents/*.md 解析。

    这是稳定的、版本可控的合约。不包含 session 级策略
    (compaction, system reminders 等由 AgentBuilder 在构建时注入)。

    借鉴 Grok Build 的 AgentDefinition，精简为 zall 所需的字段。

    YAML frontmatter 格式:
        ---
        name: my-agent
        description: A custom agent
        toolset: zall
        permissionMode: default
        model: inherit
        skills: [python, docker]
        mcpServers: [slack, github]
        mcpInheritance: all
        discoverSkills: true
        allowedSubagentTypes: null
        disallowedTools: []
        tools: []
        ---

        Agent body goes here (optional)...
    """

    model_config = ConfigDict(
        extra="forbid",
        # 让 Python 代码可以用 snake_case 属性名
        populate_by_name=True,
    )

    # ── 核心标识 ──
    name: str
    description: str = ""

    # ── 工具与权限 ──
    toolset: ToolsetPreset = ToolsetPreset.ZALL
    """工具集预设"""
    permission_mode: PermissionMode = PermissionMode.DEFAULT
    """权限模式"""
    capability_mode: Optional[SubagentCapabilityMode] = None
    """能力模式限制 (用于子 agent 场景)"""

    # ── 模型 ──
    model: Optional[str] = None
    """模型名称, None=inherit (继承父 agent)"""

    # ── 技能 ──
    skills: list[str] = []
    """启用的技能列表"""
    discover_skills: bool = True
    """是否自动发现技能"""

    # ── MCP ──
    mcp_servers: list[str] = []
    """MCP 服务器引用列表"""
    mcp_inheritance: str = "all"
    """MCP 继承策略: all | none | named"""

    # ── 工具 allow/deny ──
    disallowed_tools: list[str] = []
    """工具黑名单"""
    tools: list[str] = []
    """工具白名单 (空=继承全部)"""

    # ── 子 agent ──
    allowed_subagent_types: Optional[list[str]] = None
    """允许的子 agent 类型, None=无限制, []=禁止"""

    # ── 元数据 ──
    source_path: Optional[str] = None
    """定义文件路径"""
    scope: AgentScope = AgentScope.BUILTIN
    """定义来源范围"""
    prompt_body: Optional[str] = None
    """Markdown 主体 (agent 行为提示)"""

    # ── 解析 ──

    @classmethod
    def from_file(cls, path: str | Path) -> "AgentDefinition":
        """从 .md 文件解析 AgentDefinition (YAML frontmatter + body)。

        File format:
            ---
            name: my-agent
            description: A custom agent
            ---

            System prompt body goes here...
        """
        path = Path(path)
        content = path.read_text(encoding="utf-8")
        def_ = cls._parse(content)
        def_.source_path = str(path.resolve())
        def_.scope = cls._scope_from_path(path)
        return def_

    @classmethod
    def parse_yaml(cls, yaml_content: str) -> "AgentDefinition":
        """仅从 YAML 字符串解析 AgentDefinition (无 body)。"""
        import yaml as _yaml
        data = _yaml.safe_load(yaml_content)
        if not isinstance(data, dict):
            raise ValueError("YAML frontmatter must be a mapping")
        data = _normalize_keys(data)
        return cls(**data)

    @classmethod
    def _parse(cls, content: str) -> "AgentDefinition":
        """解析完整内容 (YAML frontmatter + body)。"""
        trimmed = content.strip()
        if not trimmed.startswith("---"):
            raise ValueError(
                "Agent definition must start with '---' YAML frontmatter"
            )

        after_opening = trimmed[3:]
        closing_idx = after_opening.find("\n---")
        if closing_idx == -1:
            raise ValueError(
                "Missing closing '---' delimiter in YAML frontmatter"
            )

        yaml_section = after_opening[:closing_idx]
        after_closing = after_opening[closing_idx + 4:]
        body_start = after_closing.find("\n")
        body = (
            after_closing[body_start:].strip()
            if body_start >= 0
            else ""
        )

        import yaml as _yaml
        data = _yaml.safe_load(yaml_section)
        if not isinstance(data, dict):
            raise ValueError("YAML frontmatter must be a mapping")

        data = _normalize_keys(data)
        prompt_body = body if body else None
        return cls(**data, prompt_body=prompt_body)

    @staticmethod
    def _scope_from_path(path: Path) -> AgentScope:
        """根据路径判断定义来源范围。"""
        path_str = path.as_posix()
        # User-level: ~/.zall/agents/
        home = Path.home()
        user_agents = home / ".zall" / "agents"
        try:
            if user_agents in path.parents:
                return AgentScope.USER
        except ValueError:
            pass

        if ".zall/bundled/agents" in path_str:
            return AgentScope.BUNDLED
        if ".zall/agents" in path_str:
            return AgentScope.PROJECT
        return AgentScope.BUILTIN

    # ── 工厂方法: 内置 agent ──

    @classmethod
    def builtin_defaults(
        cls, name: str, description: str = "",
    ) -> "AgentDefinition":
        """内置 agent 的默认值。"""
        return cls(
            name=name,
            description=description,
            toolset=ToolsetPreset.ZALL,
            permission_mode=PermissionMode.DEFAULT,
            skills=[],
            discover_skills=True,
            mcp_servers=[],
            mcp_inheritance="all",
            disallowed_tools=[],
            tools=[],
            allowed_subagent_types=None,
            model=None,
            scope=AgentScope.BUILTIN,
        )

    @classmethod
    def default_zall(cls) -> "AgentDefinition":
        """默认 zall agent — 全功能。"""
        return cls.builtin_defaults(
            "zall",
            "Default zall agent for software engineering tasks.",
        )

    @classmethod
    def explore(cls) -> "AgentDefinition":
        """Explore subagent — 快速只读探索。"""
        return cls(
            name="explore",
            description=(
                "Fast, read-only codebase exploration agent. "
                "Can read files, search code, and list directories. "
                "Cannot modify files or run commands."
            ),
            toolset=ToolsetPreset.EXPLORE,
            permission_mode=PermissionMode.PLAN,
            capability_mode=SubagentCapabilityMode.READ_ONLY,
            allowed_subagent_types=[],
            discover_skills=False,
            scope=AgentScope.BUILTIN,
        )

    @classmethod
    def plan(cls) -> "AgentDefinition":
        """Plan subagent — 只读规划。"""
        return cls(
            name="plan",
            description=(
                "Read-only planning agent. Can explore codebase "
                "and maintain a todo list. Cannot modify files."
            ),
            toolset=ToolsetPreset.PLAN,
            permission_mode=PermissionMode.PLAN,
            capability_mode=SubagentCapabilityMode.PLAN_ONLY,
            allowed_subagent_types=[],
            discover_skills=False,
            scope=AgentScope.BUILTIN,
        )

    @classmethod
    def general_purpose(cls) -> "AgentDefinition":
        """General-purpose subagent — 全功能实现。"""
        return cls(
            name="general-purpose",
            description=(
                "General-purpose implementation agent. "
                "Can read, write, edit files and run commands. "
                "Use for multi-step implementation tasks."
            ),
            toolset=ToolsetPreset.ZALL,
            permission_mode=PermissionMode.DEFAULT,
            allowed_subagent_types=[],
            discover_skills=False,
            scope=AgentScope.BUILTIN,
        )


# ═══════════════════════════════════════════════════════════════════
# §6  Agent discovery — 发现 .zall/agents/ 中的 agent 定义
# ═══════════════════════════════════════════════════════════════════


def discover_agents(
    project_dir: str | None = None,
) -> list[AgentDefinition]:
    """发现所有可用的 agent 定义。

    搜索顺序 (优先级从高到低):
      1. <project>/.zall/agents/*.md
      2. ~/.zall/agents/*.md
      3. ~/.zall/bundled/agents/*.md

    Returns:
        AgentDefinition 列表 (按优先级排序)
    """
    agents: list[tuple[AgentScope, Path, AgentDefinition]] = []
    errors: list[str] = []

    search_dirs: list[Path] = []
    home = Path.home()

    # 1. Project
    if project_dir:
        search_dirs.append(Path(project_dir) / ZALL_AGENTS_DIR)
    # 2. User
    search_dirs.append(home / ".zall" / "agents")
    # 3. Bundled
    search_dirs.append(home / ".zall" / "bundled" / "agents")

    seen_names: set[str] = set()

    for search_dir in search_dirs:
        if not search_dir.is_dir():
            continue
        try:
            for fpath in sorted(search_dir.iterdir()):
                if fpath.suffix.lower() not in (".md", ".yaml", ".yml"):
                    continue
                try:
                    def_ = AgentDefinition.from_file(fpath)
                    # 去重: 同名只保留优先级最高的
                    if def_.name not in seen_names:
                        agents.append((def_.scope, fpath, def_))
                        seen_names.add(def_.name)
                except Exception as e:
                    errors.append(f"  skip {fpath.name}: {e}")
        except OSError as e:
            errors.append(f"  error reading {search_dir}: {e}")

    # 按范围优先级排序
    scope_order = {
        AgentScope.PROJECT: 0,
        AgentScope.USER: 1,
        AgentScope.BUNDLED: 2,
        AgentScope.BUILTIN: 3,
    }
    agents.sort(key=lambda x: scope_order.get(x[0], 99))

    return [def_ for _, _, def_ in agents]


def get_named_agent(
    name: str,
    project_dir: str | None = None,
) -> AgentDefinition | None:
    """按名称查找 agent 定义 (搜索所有范围)。"""
    for def_ in discover_agents(project_dir):
        if def_.name == name:
            return def_
    # 回退到内置 agent
    builtin = _BUILTIN_AGENTS.get(name)
    if builtin is not None:
        return builtin()
    return None


# 内置 agent 注册表
_BUILTIN_AGENTS: dict[str, callable] = {
    "zall": AgentDefinition.default_zall,
    "explore": AgentDefinition.explore,
    "plan": AgentDefinition.plan,
    "general-purpose": AgentDefinition.general_purpose,
}

# 公开的子 agent 类型 (供 spawn_subagent 的 schema 描述使用)
SUBAGENT_VARIANTS: list[AgentDefinition] = [
    AgentDefinition.general_purpose(),
    AgentDefinition.explore(),
    AgentDefinition.plan(),
]


def is_subagent_type(name: str) -> bool:
    """检查名称是否为内置子 agent 类型。"""
    return any(a.name == name for a in SUBAGENT_VARIANTS)
