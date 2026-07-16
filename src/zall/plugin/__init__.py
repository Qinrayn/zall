"""zall.plugin — Plugin system for zall.

Inspired by Grok Build's xai-grok-agent plugin system. A plugin is a
self-contained directory that bundles skills, agent definitions, MCP server
configs, and lifecycle hooks into a namespaced unit.

Architecture:
  ┌──────────────────────────────────────────────────────┐
  │  PluginSystem                                         │
  │  ┌──────────┐  ┌──────────┐  ┌────────────────────┐ │
  │  │ Discovery│→ │ Registry │→ │ LoadedPlugin       │ │
  │  │ (scan FS)│  │ (mem)    │  │  - manifest        │ │
  │  └──────────┘  └──────────┘  │  - skills          │ │
  │                              │  - agents          │ │
  │  ┌──────────┐  ┌──────────┐ │  - mcp_servers     │ │
  │  │ Install  │  │ Trust    │ │  - hooks           │ │
  │  │ (git)    │  │ (verify) │ └────────────────────┘ │
  │  └──────────┘  └──────────┘                        │
  └──────────────────────────────────────────────────────┘

Plugin directory structure:
  <plugin-dir>/
    plugin.toml           # Plugin manifest (required)
    skills/               # Skill definitions (*.md)
    agents/               # Agent definitions (*.md)
    mcp.toml              # MCP server configs
    hooks/                # Lifecycle hook scripts
    __init__.py           # Optional Python entry point

Usage:
    system = PluginSystem()
    system.discover()
    plugins = system.load_all()
    for plugin in plugins:
        print(plugin.name, plugin.skills, plugin.agents)

IPR constraints:
  IPR-0: invariant tests at tests/test_plugin_system.py
  IPR-3: stdlib / pydantic only, no model SDK
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional, Protocol


# ═══════════════════════════════════════════════════════════════════
# §1  Plugin Manifest
# ═══════════════════════════════════════════════════════════════════


@dataclass
class PluginManifest:
    """插件清单 — 从 plugin.toml 解析。

    Fields:
        name: 插件名称 (命名空间)
        version: 语义版本号
        description: 描述
        author: 作者
        license: 许可证
        min_zall_version: 最低 zall 版本要求
        skills: 包含的技能列表 (文件名)
        agents: 包含的 agent 定义列表 (文件名)
        mcp_servers: MCP 服务器配置列表
        hooks: 生命周期钩子配置
        dependencies: 插件依赖 (其他插件名)
    """
    name: str
    version: str = "0.1.0"
    description: str = ""
    author: str = ""
    license: str = ""
    min_zall_version: str = "0.3.0"
    skills: list[str] = field(default_factory=list)
    agents: list[str] = field(default_factory=list)
    mcp_servers: list[str] = field(default_factory=list)
    hooks: dict[str, str] = field(default_factory=dict)
    dependencies: list[str] = field(default_factory=list)

    @classmethod
    def from_file(cls, path: str | Path) -> PluginManifest:
        """从 plugin.toml 文件解析。"""
        path = Path(path)
        content = path.read_text(encoding="utf-8")
        return cls._parse_toml(content, path)

    @classmethod
    def _parse_toml(cls, content: str, path: Path) -> PluginManifest:
        """解析 TOML 内容。"""
        try:
            import tomllib  # Python 3.11+
        except ImportError:
            try:
                import tomli as tomllib  # noqa: N813
            except ImportError:
                raise ImportError(
                    "tomli/tomllib required for plugin manifest parsing. "
                    "Install with: pip install tomli"
                )

        data = tomllib.loads(content)
        plugin = data.get("plugin", {})
        if not plugin.get("name"):
            raise ValueError(f"Plugin manifest missing required 'plugin.name' field: {path}")

        return cls(
            name=plugin["name"],
            version=plugin.get("version", "0.1.0"),
            description=plugin.get("description", ""),
            author=plugin.get("author", ""),
            license=plugin.get("license", ""),
            min_zall_version=plugin.get("min_zall_version", "0.3.0"),
            skills=plugin.get("skills", []),
            agents=plugin.get("agents", []),
            mcp_servers=plugin.get("mcp_servers", []),
            hooks=plugin.get("hooks", {}),
            dependencies=plugin.get("dependencies", []),
        )


# ═══════════════════════════════════════════════════════════════════
# §2  Plugin Origin & Scope
# ═══════════════════════════════════════════════════════════════════


class PluginOrigin(str):
    """插件来源路径。"""
    pass


class PluginScope(Enum):
    """插件作用域 — 决定优先级。"""
    PROJECT = "project"
    USER = "user"
    MARKETPLACE = "marketplace"
    BUILTIN = "built-in"


# ═══════════════════════════════════════════════════════════════════
# §3  Discovered Plugin
# ═══════════════════════════════════════════════════════════════════


@dataclass
class DiscoveredPlugin:
    """发现的插件 — 尚未加载。"""
    name: str
    path: Path
    manifest: PluginManifest
    scope: PluginScope

    @property
    def skills_dir(self) -> Path | None:
        d = self.path / "skills"
        return d if d.is_dir() else None

    @property
    def agents_dir(self) -> Path | None:
        d = self.path / "agents"
        return d if d.is_dir() else None

    @property
    def hooks_dir(self) -> Path | None:
        d = self.path / "hooks"
        return d if d.is_dir() else None

    @property
    def mcp_config_path(self) -> Path | None:
        p = self.path / "mcp.toml"
        return p if p.is_file() else None


# ═══════════════════════════════════════════════════════════════════
# §4  Loaded Plugin
# ═══════════════════════════════════════════════════════════════════


@dataclass
class LoadedPlugin:
    """已加载的插件 — 包含所有资源。"""
    name: str
    manifest: PluginManifest
    path: Path
    scope: PluginScope
    skills: list[Any] = field(default_factory=list)
    """加载的技能定义列表"""
    agents: list[Any] = field(default_factory=list)
    """加载的 agent 定义列表"""
    mcp_servers: list[Any] = field(default_factory=list)
    """MCP 服务器配置"""
    hooks: dict[str, Any] = field(default_factory=dict)
    """生命周期钩子"""
    entry_point: Any = None
    """Python 入口点模块 (如果有 __init__.py)"""


# ═══════════════════════════════════════════════════════════════════
# §5  Plugin Discovery
# ═══════════════════════════════════════════════════════════════════


def discover_plugin_dirs(
    project_dir: str | None = None,
) -> list[tuple[Path, PluginScope]]:
    """发现所有插件目录。

    搜索顺序:
      1. <project>/.zall/plugins/
      2. ~/.zall/plugins/
      3. ~/.zall/bundled/plugins/

    Returns:
        [(path, scope), ...] 按优先级降序
    """
    dirs: list[tuple[Path, PluginScope]] = []
    home = Path.home()

    if project_dir:
        dirs.append((Path(project_dir) / ".zall" / "plugins", PluginScope.PROJECT))
    dirs.append((home / ".zall" / "plugins", PluginScope.USER))
    dirs.append((home / ".zall" / "bundled" / "plugins", PluginScope.MARKETPLACE))

    return [(p, s) for p, s in dirs if p.is_dir()]


def discover_plugins(
    project_dir: str | None = None,
) -> list[DiscoveredPlugin]:
    """发现所有可用的插件。

    Returns:
        DiscoveredPlugin 列表 (按范围优先级排序)
    """
    plugins: list[DiscoveredPlugin] = []
    seen_names: set[str] = set()

    scope_order = {
        PluginScope.PROJECT: 0,
        PluginScope.USER: 1,
        PluginScope.MARKETPLACE: 2,
        PluginScope.BUILTIN: 3,
    }

    for search_dir, scope in discover_plugin_dirs(project_dir):
        try:
            for entry in sorted(search_dir.iterdir()):
                if not entry.is_dir():
                    continue
                manifest_path = entry / "plugin.toml"
                if not manifest_path.is_file():
                    continue
                try:
                    manifest = PluginManifest.from_file(manifest_path)
                    if manifest.name not in seen_names:
                        plugins.append(DiscoveredPlugin(
                            name=manifest.name,
                            path=entry,
                            manifest=manifest,
                            scope=scope,
                        ))
                        seen_names.add(manifest.name)
                except Exception as e:
                    print(f"  [plugin] skip {entry.name}: {e}", file=sys.stderr)
        except OSError:
            pass

    plugins.sort(key=lambda p: scope_order.get(p.scope, 99))
    return plugins


# ═══════════════════════════════════════════════════════════════════
# §6  Plugin Loader
# ═══════════════════════════════════════════════════════════════════


class PluginLoader:
    """插件加载器 — 从 DiscoveredPlugin 加载资源。"""

    def __init__(self) -> None:
        self._loaded: dict[str, LoadedPlugin] = {}
        self._current_name: str = ""

    @property
    def loaded(self) -> dict[str, LoadedPlugin]:
        return dict(self._loaded)

    def load(self, plugin: DiscoveredPlugin) -> LoadedPlugin:
        """加载单个插件。"""
        if plugin.name in self._loaded:
            return self._loaded[plugin.name]

        self._current_name = plugin.name  # For skill loading

        loaded = LoadedPlugin(
            name=plugin.name,
            manifest=plugin.manifest,
            path=plugin.path,
            scope=plugin.scope,
        )

        # 加载技能
        if plugin.skills_dir:
            skills = self._load_skills(plugin.skills_dir, plugin.manifest.skills)
            loaded.skills = skills

        # 加载 agent 定义
        if plugin.agents_dir:
            agents = self._load_agents(plugin.agents_dir, plugin.manifest.agents)
            loaded.agents = agents

        # 加载 MCP 配置
        if plugin.mcp_config_path:
            mcp_servers = self._load_mcp_config(plugin.mcp_config_path)
            loaded.mcp_servers = mcp_servers

        # 加载 Python 入口点
        init_py = plugin.path / "__init__.py"
        if init_py.is_file():
            loaded.entry_point = self._load_entry_point(plugin.name, init_py)

        self._loaded[plugin.name] = loaded
        return loaded

    def load_all(self, plugins: list[DiscoveredPlugin]) -> list[LoadedPlugin]:
        """批量加载插件。"""
        return [self.load(p) for p in plugins]

    def _load_skills(
        self,
        skills_dir: Path,
        skill_names: list[str],
    ) -> list[Any]:
        """加载技能文件。"""
        skills = []
        for name in skill_names:
            path = skills_dir / name
            if not path.suffix:
                path = path.with_suffix(".md")
            if path.is_file():
                try:
                    # Try loading as markdown skill with YAML frontmatter
                    content = path.read_text(encoding="utf-8")
                    if content.strip().startswith("---"):
                        import yaml as _yaml
                        # Simple frontmatter extraction
                        parts = content.split("---", 2)
                        if len(parts) >= 3:
                            frontmatter = _yaml.safe_load(parts[1])
                            skill_name = frontmatter.get("name", path.stem)
                            skills.append({
                                "name": skill_name,
                                "description": frontmatter.get("description", ""),
                                "prompt": parts[2].strip(),
                                "plugin": self._current_name,
                            })
                except Exception:
                    pass
        return skills

    def _load_agents(
        self,
        agents_dir: Path,
        agent_names: list[str],
    ) -> list[Any]:
        """加载 agent 定义文件。"""
        from zall.core.agent import AgentDefinition
        agents = []
        for name in agent_names:
            path = agents_dir / name
            if not path.suffix:
                path = path.with_suffix(".md")
            if path.is_file():
                try:
                    agent = AgentDefinition.from_file(path)
                    agents.append(agent)
                except Exception as e:
                    print(f"  [plugin] skip agent '{name}': {e}", file=sys.stderr)
        return agents

    def _load_mcp_config(self, config_path: Path) -> list[Any]:
        """加载 MCP 配置。"""
        try:
            from zall.mcp.config import load_mcp_config
            servers = load_mcp_config(str(config_path))
            return servers
        except Exception as e:
            print(f"  [plugin] skip MCP config: {e}", file=sys.stderr)
            return []

    def _load_entry_point(self, name: str, init_py: Path) -> Any:
        """加载 Python 入口点。"""
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location(
                f"zall_plugin_{name}",
                str(init_py),
            )
            if spec and spec.loader:
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                return mod
        except Exception as e:
            print(f"  [plugin] skip entry point '{name}': {e}", file=sys.stderr)
        return None


# ═══════════════════════════════════════════════════════════════════
# §7  Plugin System
# ═══════════════════════════════════════════════════════════════════


class PluginSystem:
    """插件系统 — 统一入口点。

    管理插件的发现、加载、生命周期。

    Usage:
        system = PluginSystem()
        system.discover()
        system.load_all()
        for plugin in system.active_plugins:
            print(plugin.name, plugin.skills)
    """

    def __init__(self, project_dir: str | None = None) -> None:
        self._project_dir = project_dir or os.getcwd()
        self._discovered: list[DiscoveredPlugin] = []
        self._loader = PluginLoader()
        self._active: dict[str, LoadedPlugin] = {}

    # ── Discovery ──

    def discover(self) -> list[DiscoveredPlugin]:
        """发现插件。"""
        self._discovered = discover_plugins(self._project_dir)
        return list(self._discovered)

    @property
    def discovered(self) -> list[DiscoveredPlugin]:
        return list(self._discovered)

    # ── Loading ──

    def load_all(self) -> list[LoadedPlugin]:
        """加载所有已发现的插件。"""
        loaded = self._loader.load_all(self._discovered)
        for p in loaded:
            self._active[p.name] = p
        return list(self._active.values())

    def load_plugin(self, name: str) -> LoadedPlugin | None:
        """按名称加载单个插件。"""
        if name in self._active:
            return self._active[name]
        for dp in self._discovered:
            if dp.name == name:
                loaded = self._loader.load(dp)
                self._active[loaded.name] = loaded
                return loaded
        return None

    @property
    def active_plugins(self) -> list[LoadedPlugin]:
        return list(self._active.values())

    def get_plugin(self, name: str) -> LoadedPlugin | None:
        return self._active.get(name)

    # ── Resource navigation ──

    def get_all_skills(self) -> list[Any]:
        """获取所有插件中加载的技能。"""
        skills = []
        for plugin in self._active.values():
            skills.extend(plugin.skills)
        return skills

    def get_all_agents(self) -> list[Any]:
        """获取所有插件中加载的 agent 定义。"""
        agents = []
        for plugin in self._active.values():
            agents.extend(plugin.agents)
        return agents

    def get_all_mcp_servers(self) -> list[Any]:
        """获取所有插件中加载的 MCP 服务器。"""
        servers = []
        for plugin in self._active.values():
            servers.extend(plugin.mcp_servers)
        return servers

    # ── Lifecycle ──

    def reload(self) -> list[LoadedPlugin]:
        """重新发现并加载所有插件。"""
        self._active.clear()
        self._discovered = []
        self.discover()
        return self.load_all()

    def unload(self, name: str) -> bool:
        """卸载插件。"""
        if name in self._active:
            del self._active[name]
            return True
        return False

    # ── Install ──

    def install_from_git(
        self,
        repo_url: str,
        *,
        target_dir: str | None = None,
        branch: str = "main",
    ) -> LoadedPlugin | None:
        """从 Git 仓库安装插件。

        Args:
            repo_url: Git 仓库 URL
            target_dir: 安装目标目录 (默认 ~/.zall/plugins/)
            branch: Git 分支

        Returns:
            加载后的插件, 或 None 如果失败
        """
        target = Path(target_dir or Path.home() / ".zall" / "plugins")
        target.mkdir(parents=True, exist_ok=True)

        # 从 URL 推断插件名
        plugin_name = Path(repo_url).stem
        dest = target / plugin_name

        if dest.exists():
            # 已存在 → git pull
            try:
                subprocess.run(
                    ["git", "-C", str(dest), "pull", "origin", branch],
                    capture_output=True, text=True, timeout=60,
                )
            except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
                print(f"  [plugin] git pull failed: {e}", file=sys.stderr)
                return None
        else:
            # 克隆
            try:
                subprocess.run(
                    ["git", "clone", "--depth", "1", "-b", branch, repo_url, str(dest)],
                    capture_output=True, text=True, timeout=120,
                )
            except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
                print(f"  [plugin] git clone failed: {e}", file=sys.stderr)
                return None

        # 加载新安装的插件
        manifest_path = dest / "plugin.toml"
        if not manifest_path.is_file():
            print(f"  [plugin] no plugin.toml in {dest}", file=sys.stderr)
            return None

        try:
            manifest = PluginManifest.from_file(manifest_path)
            discovered = DiscoveredPlugin(
                name=manifest.name,
                path=dest,
                manifest=manifest,
                scope=PluginScope.USER,
            )
            return self._loader.load(discovered)
        except Exception as e:
            print(f"  [plugin] load failed: {e}", file=sys.stderr)
            return None

    def __repr__(self) -> str:
        return (
            f"PluginSystem(discovered={len(self._discovered)}, "
            f"active={len(self._active)})"
        )