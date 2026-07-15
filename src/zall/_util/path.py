"""zall._util.path — path/directoryoperation共享toolfunction。

统一各工具中的噪音目录过滤逻辑。
"""

from __future__ import annotations

from pathlib import Path
import difflib

# 统一noisedirectory集 (并集: glob.py + grep.py + list_dir.py + loop.py)
NOISE_DIRS: frozenset[str] = frozenset({
    ".git", "node_modules", ".venv", "venv", "__pycache__",
    ".tox", ".eggs", ".egg-info", ".svn", ".hg",
    ".pytest_cache", ".mypy_cache", ".ruff_cache",
    "dist", "build", "target", ".tox",
    "lib", "include",
})


def is_noise(path: Path) -> bool:
    """checkpath的任何部分是否属于noisedirectory。

    B23 统一: glob.py 的 _is_noise + grep.py 的 _SKIP_DIRS 过滤。
    """
    return any(part in NOISE_DIRS for part in path.parts)


def skip_noise_dirs(dirnames: list[str]) -> None:
    """原地filter dirnames, removenoisedirectory (用于 os.walk topdown 提前filter)。

    v0.1.1 fix (O3): 从 loop.py _maybe_checkpoint_file 抽出。
    """
    dirnames[:] = [d for d in dirnames if d not in NOISE_DIRS]


def resolve_path(path_str: str) -> Path:
    """统一pathparse: 相对path转绝对path (基于当前工作directory)。

    消除 7 个工具中重复的 `Path(path_str); if not path.is_absolute(): path = Path.cwd() / path` 模式。
    """
    path = Path(path_str)
    if not path.is_absolute():
        path = Path.cwd() / path
    return path.resolve()


def suggest_similar_path(path: Path) -> str | None:
    """当file不存在时, 找最相似的path (P1 fix: 模糊path建议)."""
    parent = path.parent
    target_name = path.name

    if parent.is_dir():
        siblings = [e.name for e in parent.iterdir() if e.is_file()]
        matches = difflib.get_close_matches(target_name, siblings, n=1, cutoff=0.6)
        if matches:
            return str(parent / matches[0])

    if not parent.exists() and str(parent) != parent.anchor:
        grandparent = parent.parent
        if grandparent.is_dir():
            dir_siblings = [e.name for e in grandparent.iterdir() if e.is_dir()]
            dir_matches = difflib.get_close_matches(parent.name, dir_siblings, n=1, cutoff=0.6)
            if dir_matches:
                suggested_dir = grandparent / dir_matches[0]
                suggested_path = suggested_dir / target_name
                if suggested_path.is_file():
                    return str(suggested_path)
                file_siblings = [e.name for e in suggested_dir.iterdir() if e.is_file()]
                file_matches = difflib.get_close_matches(target_name, file_siblings, n=1, cutoff=0.6)
                if file_matches:
                    return str(suggested_dir / file_matches[0])

    return None