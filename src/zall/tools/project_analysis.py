"""zall.tools.project_analysis — 项目分析工具 (v0.4.0).

分析项目结构: 语言分布、文件统计、目录树。
Agent 可用此工具快速了解项目全貌。

IPR constraints:
  IPR-0: invariant tests
  IPR-3: stdlib only
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from zall.core.tool import ToolResult


# 语言 -> 扩展名映射
_LANG_EXT: dict[str, set[str]] = {
    "Python": {".py"},
    "JavaScript": {".js", ".jsx", ".mjs", ".cjs"},
    "TypeScript": {".ts", ".tsx"},
    "Rust": {".rs"},
    "Go": {".go"},
    "Java": {".java", ".kt"},
    "C/C++": {".c", ".cpp", ".cc", ".cxx", ".h", ".hpp", ".hh"},
    "Ruby": {".rb"},
    "PHP": {".php"},
    "Swift": {".swift"},
    "TOML": {".toml"},
    "YAML": {".yaml", ".yml"},
    "JSON": {".json"},
    "Markdown": {".md"},
    "CSS": {".css"},
    "HTML": {".html"},
}

# 默认跳过的目录
_SKIP_DIRS = frozenset({
    ".git", "__pycache__", "node_modules", "venv", ".venv",
    ".tox", "dist", "build", ".egg-info", "target",
    ".pytest_cache", ".ruff_cache", ".mypy_cache",
    ".zall", ".zcode", ".idea", ".vscode",
    "vendor", "bundle", ".bundle", ".git",
})


class ProjectAnalysisTool:
    """项目分析工具 — 分析项目结构。

    返回: 语言分布、文件统计、目录树。
    """

    __test__ = False

    def __init__(self, codegraph: Any | None = None) -> None:
        self._cg = codegraph

    def set_codegraph(self, codegraph: Any) -> None:
        self._cg = codegraph

    @property
    def tool_id(self) -> str:
        return "project_analysis"

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "project_analysis",
                "description": (
                    "Analyze the project structure: file counts by language, "
                    "total lines of code, and a directory tree overview. "
                    "Use this at the start of a project to understand "
                    "what you're working with — what languages, frameworks, "
                    "and major directories are present."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "depth": {
                            "type": "integer",
                            "description": "Directory tree depth (default: 3)",
                            "default": 3,
                        },
                        "include_codegraph": {
                            "type": "boolean",
                            "description": "Include CodeGraph symbol stats (default: true)",
                            "default": True,
                        },
                    },
                    "required": [],
                },
            },
        }

    def execute(self, args: dict[str, Any]) -> ToolResult:
        depth = int(args.get("depth", 3))
        include_cg = args.get("include_codegraph", True)

        try:
            cwd = Path.cwd()
            result = self._analyze(cwd, depth, include_cg)
            return ToolResult(
                success=True,
                output=result["output"],
                artifacts=result.get("artifacts", {}),
            )
        except Exception as e:
            return ToolResult(
                success=False, output="", error=str(e),
            )

    def _analyze(
        self, root: Path, depth: int, include_cg: bool,
    ) -> dict[str, Any]:
        """执行项目分析。"""
        parts: list[str] = []
        artifacts: dict[str, Any] = {}

        total_files = 0
        total_lines = 0
        lang_counts: dict[str, int] = {}
        lang_lines: dict[str, int] = {}
        dir_structure: list[str] = []

        for dirpath, dirnames, filenames in _walk(root):
            # Skip
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS
                          and not d.startswith(".")]

            rel = Path(dirpath).relative_to(root)
            parts_rel = rel.parts

            # Directory tree (up to depth)
            if len(parts_rel) < depth:
                indent = "  " * len(parts_rel)
                dir_structure.append(f"{indent}{rel.name}/")

            for fn in filenames:
                ext = Path(fn).suffix.lower()
                lang = self._detect_language(ext)
                if lang:
                    total_files += 1
                    lang_counts[lang] = lang_counts.get(lang, 0) + 1

                    # Estimate lines
                    try:
                        fpath = Path(dirpath) / fn
                        with open(fpath, encoding="utf-8", errors="replace") as f:
                            line_count = sum(1 for _ in f)
                        total_lines += line_count
                        lang_lines[lang] = lang_lines.get(lang, 0) + line_count
                    except OSError:
                        pass

                # Directory tree files (up to depth)
                if len(parts_rel) < depth:
                    indent = "  " * (len(parts_rel) + 1)
                    dir_structure.append(f"{indent}{fn}")

        # Summary
        parts.append(f"[Project Analysis: {root.name}]")
        parts.append(f"\n  📊 Overview:")
        parts.append(f"    Total files: {total_files}")
        parts.append(f"    Total lines:  ~{total_lines:,}")

        # Language breakdown
        parts.append(f"\n  🔤 Languages:")
        for lang in sorted(lang_counts.keys(), key=lambda l: -lang_counts[l]):
            files = lang_counts[lang]
            lines = lang_lines.get(lang, 0)
            pct = f"({files * 100 // total_files}%)" if total_files else ""
            parts.append(f"    {lang:15s}  {files:4d} files  ~{lines:,} lines  {pct}")

        # Directory tree
        if dir_structure:
            parts.append(f"\n  📁 Structure:")
            parts.extend(dir_structure[:40])  # Limit output
            if len(dir_structure) > 40:
                parts.append(f"    ... ({len(dir_structure) - 40} more entries)")

        # CodeGraph stats
        if include_cg and self._cg is not None:
            try:
                stats = self._cg.get_stats()
                if stats.get("status") == "indexed":
                    parts.append(f"\n  🔍 CodeGraph:")
                    parts.append(f"    Symbols: {stats.get('symbol_count', 0)}")
                    parts.append(f"    Files:   {stats.get('file_count', 0)}")
                    parts.append(f"    Errors:  {stats.get('error_count', 0)}")
                    parts.append(f"    Use /codegraph search <q> to find symbols")
            except Exception:
                pass

        artifacts["total_files"] = total_files
        artifacts["total_lines"] = total_lines
        artifacts["languages"] = dict(lang_counts)
        artifacts["language_lines"] = dict(lang_lines)

        return {"output": "\n".join(parts), "artifacts": artifacts}

    def _detect_language(self, extension: str) -> str | None:
        """根据扩展名检测语言。"""
        for lang, exts in _LANG_EXT.items():
            if extension in exts:
                return lang
        return None


def _walk(root: Path):
    """os.walk 封装。"""
    import os
    for dirpath, dirnames, filenames in os.walk(str(root), topdown=True):
        yield Path(dirpath), dirnames, filenames