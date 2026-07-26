"""zall.core.perception.coding_sensors — Coding agent 传感器实现。

Corresponds to:
  §4.2.3  Perception Engine: 传感器实现
  MASTER.md §6.1 Coding Kit: 编码领域的感知

提供 Coding agent 专用的传感器:
  - FileSensor:     文件系统变化感知 (修改时间/内容)
  - GitSensor:      Git 状态感知 (diff/status/log)
  - CodeGraphSensor: 代码结构感知 (符号/引用)
  - LSPSensor:      实时诊断感知 (错误/警告)

每个传感器实现 Sensor Protocol, 返回 Observation 对象。
传感器只做"观测", 不做"解释"——解释由 PerceptionEngine 的融合逻辑完成。

IPR constraints:
  IPR-3: stdlib + pydantic only, no model SDK
  IPR-0: sensor failures must not crash the agent (degraded observation)
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

from zall.core.perception.sensor import Observation, Sensor


# ──────────────────────────────────────────────────────────────────────────
# FileSensor — 文件系统传感器
# ──────────────────────────────────────────────────────────────────────────


class FileSensor:
    """文件系统传感器——感知工作目录的文件变化。

    追踪:
      - 已修改的文件列表 (按修改时间)
      - 新增的文件列表
      - 文件大小变化
      - 关键文件的存在性

    Usage:
        sensor = FileSensor(project_root="/path/to/project")
        obs = sensor.observe()
        # obs.data == {"modified_files": [...], "new_files": [...], ...}

    IPR-0: 项目根目录不存在时, 返回置信度 0 的观测 (不崩溃)。
    """

    __test__ = False  # 防 pytest 收集

    def __init__(
        self,
        project_root: str | None = None,
        tracked_extensions: frozenset[str] | None = None,
    ) -> None:
        self._project_root = Path(project_root) if project_root else Path.cwd()
        self._tracked_extensions = tracked_extensions or frozenset({
            ".py", ".js", ".ts", ".rs", ".go", ".java",
            ".md", ".toml", ".yaml", ".yml", ".json",
            ".css", ".html", ".c", ".cpp", ".h", ".hpp",
        })
        # 缓存上次扫描结果
        self._last_scan: dict[str, float] = {}  # path -> mtime

    @property
    def sensor_id(self) -> str:
        return "file"

    def describe(self) -> str:
        return f"FileSensor(root={self._project_root})"

    def observe(self) -> Observation:
        """扫描文件系统, 返回文件变化观测。

        Returns:
            Observation 包含:
              - modified_files: 修改过的文件列表
              - new_files: 新增的文件列表
              - total_files: 总文件数
              - tracked_extensions: 追踪的文件扩展名
        """
        if not self._project_root.is_dir():
            return Observation.create(
                sensor_id="file",
                data={"error": f"project_root not found: {self._project_root}"},
                confidence=0.0,
                metadata={"project_root": str(self._project_root)},
            )

        try:
            current_scan: dict[str, float] = {}
            modified: list[str] = []
            new_files: list[str] = []

            for dirpath, dirnames, filenames in os.walk(str(self._project_root), topdown=True):
                # 跳过噪声目录
                dirnames[:] = [d for d in dirnames
                               if not d.startswith((".", "_"))
                               and d not in ("node_modules", "__pycache__", "venv", ".venv")]

                rel_base = os.path.relpath(dirpath, str(self._project_root))
                for fn in filenames:
                    ext = os.path.splitext(fn)[1].lower()
                    if ext not in self._tracked_extensions:
                        continue
                    fpath = os.path.join(dirpath, fn)
                    try:
                        mtime = os.path.getmtime(fpath)
                    except OSError:
                        continue
                    rel = os.path.join(rel_base, fn) if rel_base != "." else fn
                    rel_norm = rel.replace("\\", "/")
                    current_scan[rel_norm] = mtime

                    if rel_norm in self._last_scan:
                        if mtime > self._last_scan[rel_norm]:
                            modified.append(rel_norm)
                    else:
                        new_files.append(rel_norm)

            self._last_scan = current_scan

            return Observation.create(
                sensor_id="file",
                data={
                    "modified_files": modified[:50],  # 限制条目数
                    "new_files": new_files[:50],
                    "total_files": len(current_scan),
                    "tracked_extensions": sorted(self._tracked_extensions),
                },
                confidence=0.95,  # 文件系统观测确定性高
                metadata={"project_root": str(self._project_root)},
            )
        except Exception as _e:
            return Observation.create(
                sensor_id="file",
                data={"error": str(_e)},
                confidence=0.0,
            )


# ──────────────────────────────────────────────────────────────────────────
# GitSensor — Git 状态传感器
# ──────────────────────────────────────────────────────────────────────────


class GitSensor:
    """Git 状态传感器——感知仓库的 Git 状态。

    追踪:
      - 当前分支
      - 未暂存的文件修改
      - 未追踪的文件
      - 最近提交信息
      - 与远程的差异

    Usage:
        sensor = GitSensor(project_root="/path/to/repo")
        obs = sensor.observe()
        # obs.data == {"branch": "main", "modified": [...], ...}

    IPR-0: git 命令失败时返回置信度 0 的观测 (不崩溃)。
    """

    __test__ = False  # 防 pytest 收集

    def __init__(self, project_root: str | None = None) -> None:
        self._project_root = project_root or str(Path.cwd())

    @property
    def sensor_id(self) -> str:
        return "git"

    def describe(self) -> str:
        return f"GitSensor(root={self._project_root})"

    def observe(self) -> Observation:
        """执行 git 状态查询, 返回 Git 状态观测。"""
        try:
            # 1. 当前分支
            branch = self._git_cmd("rev-parse", "--abbrev-ref", "HEAD")
            # 2. 当前 SHA
            sha = self._git_cmd("rev-parse", "HEAD")
            # 3. 未暂存修改
            modified = self._git_diff("--name-only")
            # 4. 未追踪文件
            untracked = self._git_cmd("ls-files", "--others", "--exclude-standard")
            untracked_list = [f.strip() for f in untracked.splitlines() if f.strip()] if untracked else []
            # 5. 最近提交
            last_commit = self._git_cmd("log", "-1", "--oneline")

            return Observation.create(
                sensor_id="git",
                data={
                    "branch": branch.strip() if branch else "unknown",
                    "sha": sha.strip() if sha else "unknown",
                    "modified": [f.strip() for f in modified.splitlines() if f.strip()] if modified else [],
                    "untracked": untracked_list[:50],
                    "last_commit": last_commit.strip() if last_commit else "",
                    "has_uncommitted": bool(modified and modified.strip()),
                },
                confidence=0.95,
                metadata={"project_root": self._project_root},
            )
        except Exception as _e:
            return Observation.create(
                sensor_id="git",
                data={"error": str(_e)},
                confidence=0.0,
            )

    def _git_cmd(self, *args: str) -> str:
        """执行 git 命令, 返回 stdout。"""
        result = subprocess.run(
            ["git"] + list(args),
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10,
            cwd=self._project_root,
        )
        if result.returncode == 0:
            return result.stdout
        return ""

    def _git_diff(self, *args: str) -> str:
        """执行 git diff 命令。"""
        return self._git_cmd("diff", *args)


# ──────────────────────────────────────────────────────────────────────────
# CodeGraphSensor — 代码结构传感器
# ──────────────────────────────────────────────────────────────────────────


class CodeGraphSensor:
    """代码结构传感器——感知代码的符号结构和依赖关系。

    追踪:
      - 项目中定义的符号 (类/函数/变量)
      - 符号间的引用关系
      - 文件间的依赖

    这是一个轻量级实现, 只在首次调用时扫描。
    实际集成时建议使用 zall.codegraph 模块。

    Usage:
        sensor = CodeGraphSensor(project_root="/path/to/project")
        obs = sensor.observe()
        # obs.data == {"symbols": [...], "references": [...], ...}

    IPR-0: codegraph 不可用时返回置信度 0 的观测。
    """

    __test__ = False  # 防 pytest 收集

    def __init__(self, project_root: str | None = None) -> None:
        self._project_root = project_root or str(Path.cwd())
        self._cached_symbols: list[dict[str, Any]] = []

    @property
    def sensor_id(self) -> str:
        return "codegraph"

    def describe(self) -> str:
        return f"CodeGraphSensor(root={self._project_root})"

    def observe(self) -> Observation:
        """扫描代码结构, 返回符号索引观测。

        尝试使用 zall.codegraph 模块, 如果不可用则降级。
        """
        try:
            # 索引项目符号 (CodeIndexer 直接产出 CodeIndex; 无需 CodeGraph 包装)
            from zall.codegraph import CodeIndexer
            indexer = CodeIndexer()
            index = indexer.index_project(self._project_root)

            symbols = [s for syms in index.by_file.values() for s in syms][:100]  # 限制条目
            self._cached_symbols = [
                {
                    "name": s.name,
                    "kind": s.kind,
                    "file": s.location.file_path,
                    "line": s.location.start_line,
                }
                for s in symbols
            ]

            return Observation.create(
                sensor_id="codegraph",
                data={
                    "symbols": self._cached_symbols[:50],
                    "total_symbols": len(self._cached_symbols),
                    "languages": sorted({
                        s["file"].rsplit(".", 1)[-1]
                        for s in self._cached_symbols if "." in s["file"]
                    }),
                },
                confidence=0.9,
                metadata={"project_root": self._project_root},
            )
        except ImportError:
            # codegraph 不可用 → 降级
            return Observation.create(
                sensor_id="codegraph",
                data={"available": False, "reason": "codegraph module not available"},
                confidence=0.2,
            )
        except Exception as _e:
            return Observation.create(
                sensor_id="codegraph",
                data={"error": str(_e)},
                confidence=0.0,
            )


# ──────────────────────────────────────────────────────────────────────────
# LSPSensor — LSP 诊断传感器
# ──────────────────────────────────────────────────────────────────────────


class LSPSensor:
    """LSP 诊断传感器——感知代码中的实时诊断信息。

    追踪:
      - 错误 (error) 数
      - 警告 (warning) 数
      - 信息 (info) 数
      - 按文件分组的诊断列表

    Usage:
        sensor = LSPSensor()
        obs = sensor.observe()
        # obs.data == {"errors": 3, "warnings": 5, ...}

    IPR-0: LSP 不可用时返回置信度 0 的观测。
    """

    __test__ = False  # 防 pytest 收集

    def __init__(self) -> None:
        self._lsp_manager: Any | None = None
        self._lsp_available = False

    @property
    def sensor_id(self) -> str:
        return "lsp"

    def describe(self) -> str:
        return f"LSPSensor(available={self._lsp_available})"

    def observe(self) -> Observation:
        """查询 LSP 诊断, 返回代码质量观测。"""
        try:
            from zall.lsp import LspManager
            if self._lsp_manager is None:
                self._lsp_manager = LspManager()
                self._lsp_available = True

            diagnostics = self._lsp_manager.get_all_diagnostics()
            errors = [d for d in diagnostics if d.severity == "error"]
            warnings = [d for d in diagnostics if d.severity == "warning"]
            infos = [d for d in diagnostics if d.severity == "info"]

            # 按文件分组
            by_file: dict[str, int] = {}
            for d in diagnostics:
                f = d.file_path
                by_file[f] = by_file.get(f, 0) + 1

            return Observation.create(
                sensor_id="lsp",
                data={
                    "errors": len(errors),
                    "warnings": len(warnings),
                    "infos": len(infos),
                    "total_diagnostics": len(diagnostics),
                    "files_with_issues": sorted(by_file.items(), key=lambda x: -x[1])[:20],
                },
                confidence=0.85,
                metadata={"diagnostics_count": len(diagnostics)},
            )
        except ImportError:
            return Observation.create(
                sensor_id="lsp",
                data={"available": False, "reason": "LSP module not available"},
                confidence=0.2,
            )
        except Exception as _e:
            return Observation.create(
                sensor_id="lsp",
                data={"error": str(_e)},
                confidence=0.0,
            )


# ──────────────────────────────────────────────────────────────────────────
# CompositeSensor — 多传感器组合
# ──────────────────────────────────────────────────────────────────────────


class CompositeSensor:
    """组合多个传感器为单一传感器。

    用于需要从多个角度观测同一事物的场景。
    内部依次调用所有子传感器, 合并 data 字典。

    Usage:
        sensor = CompositeSensor(
            sensor_id="code_quality",
            sensors=[LSPSensor(), CodeGraphSensor()],
        )
        obs = sensor.observe()
    """

    __test__ = False  # 防 pytest 收集

    def __init__(
        self,
        sensor_id: str,
        sensors: list[Sensor],
    ) -> None:
        self._sensor_id = sensor_id
        self._sensors = sensors

    @property
    def sensor_id(self) -> str:
        return self._sensor_id

    def describe(self) -> str:
        subs = ", ".join(s.sensor_id for s in self._sensors)
        return f"CompositeSensor({self._sensor_id}: [{subs}])"

    def observe(self) -> Observation:
        """依次调用所有子传感器, 合并 data 字典。

        置信度取各子传感器的最小值 (最坏情况)。
        """
        if not self._sensors:
            return Observation.create(
                sensor_id=self._sensor_id,
                data={},
                confidence=0.0,
            )

        merged_data: dict[str, Any] = {}
        min_confidence = 1.0

        for sensor in self._sensors:
            try:
                obs = sensor.observe()
                merged_data[obs.sensor_id] = obs.data
                if obs.confidence < min_confidence:
                    min_confidence = obs.confidence
            except Exception:
                merged_data[sensor.sensor_id] = {"error": "sensor_failed"}
                min_confidence = 0.0

        return Observation.create(
            sensor_id=self._sensor_id,
            data=merged_data,
            confidence=min_confidence,
        )