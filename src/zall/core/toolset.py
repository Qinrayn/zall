"""zall.core.toolset — 工具集预设系统。

借鉴 Grok Build 的 toolset presets。每个预设定义一组工具 ID，
AgentBuilder 根据预设构建 ToolRegistry。

预设列表:
  ZALL      — 全功能 (默认)
  EXPLORE   — 只读探索 (read_file + grep + list_dir + glob + search)
  PLAN      — 规划 (read + grep + list_dir + glob + search + todo)
  CODEX     — Codex 兼容 (bash + read + apply_patch + grep)
  OPENCODE  — OpenCode 兼容 (bash + read + write + edit + grep + glob + skill + todo)

IPR constraints:
  IPR-0: invariant tests at tests/test_toolset_presets.py
  IPR-3: stdlib / pydantic only, no model SDK
"""

from __future__ import annotations

from typing import Any


# ──────────────────────────────────────────────────────────────────────────
# 工具集预设定义 (工具 ID -> 工具类)
# ──────────────────────────────────────────────────────────────────────────

# 每个预设 = (tool_id, 工具类或 None(表示跳过))
# 注意: 工具类的导入延迟到 build_tools() 调用时,
# 避免模块导入时创建工具实例 (SpawnSubagentTool 的 ThreadPoolExecutor)。

_TOOLSETS: dict[str, list[str]] = {
    "zall": [
        "read_file",
        "write_file",
        "edit_file",
        "batch_edit",
        "bash",
        "grep",
        "glob",
        "list_dir",
        "web_fetch",
        "search",
        "read_image",
        "spawn_subagent",
        "todo_list",
    ],
    "explore": [
        "read_file",
        "grep",
        "glob",
        "list_dir",
        "search",
    ],
    "plan": [
        "read_file",
        "grep",
        "glob",
        "list_dir",
        "search",
        "todo_list",
    ],
    "codex": [
        "read_file",
        # codex 用 apply_patch 替代 write/edit
        "bash",
        "grep",
        "glob",
        "list_dir",
    ],
    "opencode": [
        "bash",
        "read_file",
        "write_file",
        "edit_file",
        "grep",
        "glob",
        "todo_list",
    ],
}


def get_tool_ids_for_preset(preset: str) -> list[str]:
    """获取预设对应的工具 ID 列表。

    Args:
        preset: 预设名称 (大小写不敏感, 下划线/中划线均可)

    Returns:
        工具 ID 列表

    Raises:
        ValueError: 未知预设
    """
    normalized = preset.strip().lower().replace("_", "-")
    # map hypen-form back to underscore form
    _NORMALIZE: dict[str, str] = {
        "zall": "zall",
        "explore": "explore",
        "plan": "plan",
        "codex": "codex",
        "opencode": "opencode",
    }
    key = _NORMALIZE.get(normalized)
    if key is None:
        valid = list(_TOOLSETS.keys())
        raise ValueError(
            f"Unknown toolset preset: '{preset}'. "
            f"Valid options: {', '.join(valid)}"
        )
    return list(_TOOLSETS[key])


def list_presets() -> list[str]:
    """列出所有可用的预设名称。"""
    return sorted(_TOOLSETS.keys())


# ──────────────────────────────────────────────────────────────────────────
# 工具实例化 (延迟加载)
# ──────────────────────────────────────────────────────────────────────────

_TOOL_CLASSES: dict[str, type | None] = {}
"""工具 ID -> 工具类的惰性映射。None = 未加载。"""


def _lazy_import_tool(tool_id: str) -> type | None:
    """延迟导入工具类。

    只在首次调用时导入, 避免启动时加载所有工具。
    """
    global _TOOL_CLASSES

    if tool_id in _TOOL_CLASSES:
        return _TOOL_CLASSES[tool_id]

    # 工具 ID -> 模块/类映射
    _TOOL_MODULES: dict[str, tuple[str, str]] = {
        "read_file": ("zall.tools.read_file", "ReadFileTool"),
        "write_file": ("zall.tools.write_file", "WriteFileTool"),
        "edit_file": ("zall.tools.edit_file", "EditFileTool"),
        "batch_edit": ("zall.tools.batch_edit", "BatchEditTool"),
        "bash": ("zall.tools.bash", "BashTool"),
        "grep": ("zall.tools.grep", "GrepTool"),
        "glob": ("zall.tools.glob", "GlobTool"),
        "list_dir": ("zall.tools.list_dir", "ListDirTool"),
        "web_fetch": ("zall.tools.web_fetch", "WebFetchTool"),
        "search": ("zall.tools.search", "SearchTool"),
        "read_image": ("zall.tools.read_image", "ReadImageTool"),
        "spawn_subagent": ("zall.tools.spawn_subagent", "SpawnSubagentTool"),
        "todo_list": ("zall.tools.todo", "TodoListTool"),
    }

    mod_info = _TOOL_MODULES.get(tool_id)
    if mod_info is None:
        _TOOL_CLASSES[tool_id] = None
        return None

    module_path, class_name = mod_info
    try:
        import importlib
        mod = importlib.import_module(module_path)
        cls = getattr(mod, class_name, None)
        _TOOL_CLASSES[tool_id] = cls
        return cls
    except (ImportError, AttributeError):
        _TOOL_CLASSES[tool_id] = None
        return None


def build_native_tools_for_preset(
    preset: str,
) -> list[Any]:
    """为预设构建原生工具实例列表。

    Args:
        preset: 预设名称

    Returns:
        工具实例列表

    Raises:
        ValueError: 预设未知或工具找不到
    """
    tool_ids = get_tool_ids_for_preset(preset)
    tools: list[Any] = []
    missing: list[str] = []

    for tid in tool_ids:
        cls = _lazy_import_tool(tid)
        if cls is None:
            missing.append(tid)
        else:
            tools.append(cls())

    if missing:
        raise ValueError(
            f"Cannot build toolset '{preset}': missing tool classes: "
            f"{', '.join(missing)}"
        )

    return tools


# ──────────────────────────────────────────────────────────────────────────
# 工具过滤 (用于 subagent 能力模式)
# ──────────────────────────────────────────────────────────────────────────


def filter_tools_by_ids(
    tools: list[Any],
    allowed_ids: list[str],
) -> list[Any]:
    """按允许的工具 ID 列表过滤工具实例。

    Args:
        tools: 工具实例列表
        allowed_ids: 允许的工具 ID 列表

    Returns:
        过滤后的工具实例列表 (只保留 allowed_ids 中的工具)
    """
    id_set = set(allowed_ids)
    return [t for t in tools if t.tool_id in id_set]