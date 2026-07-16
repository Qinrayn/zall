"""File state cache for tracking read file contents and modification state.

缓存每个文件的最后读取时间和 mtime, 支持:
  - is_file_unchanged(path) — 文件是否在上次读取后未被修改
  - get_cached_mtime(path) — 获取缓存的 mtime
  - mark_file_read(path) — 标记文件已读取
  - invalidate(path) — 使缓存失效

用途: 避免 agent 重复读取未修改的文件, 节省 API token。
当 agent 调用 read_file 后, 文件状态被缓存; 下次读取前先检查 mtime,
如果未变化则提示 agent "file unchanged, no need to re-read"。

IPR constraints:
  IPR-3: stdlib only, no model SDK
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any


class FileStateCache:
    """filestatecache — 跟踪file mtime + read时间。

    线程安全: 使用 RLock 保护内部 dict。
    缓存上限: 1000 条 (LRU 淘汰), 防长会话无界增长。
    """

    __test__ = False

    _MAX_ENTRIES = 1000

    def __init__(self) -> None:
        self._lock = threading.RLock()
        # key: str(path), value: {"mtime": float, "read_at": float, "size": int}
        self._cache: dict[str, dict[str, Any]] = {}

    def mark_file_read(self, path: Path | str) -> None:
        """标记file已read, cache其 mtime 和 size。"""
        key = str(path)
        try:
            stat = Path(path).stat()
            mtime = stat.st_mtime
            size = stat.st_size
        except OSError:
            return
        with self._lock:
            # LRU 淘汰
            if len(self._cache) >= self._MAX_ENTRIES:
                # 淘汰最旧的条目
                oldest_key = min(self._cache, key=lambda k: self._cache[k].get("read_at", 0))
                self._cache.pop(oldest_key, None)
            self._cache[key] = {"mtime": mtime, "read_at": time.time(), "size": size}

    def is_file_unchanged(self, path: Path | str) -> bool:
        """checkfile是否在上次read后未被修改。

        Returns:
            True — 文件未修改 (可以跳过重复读取)
            False — 文件已修改或未被缓存过 (需要重新读取)
        """
        key = str(path)
        with self._lock:
            cached = self._cache.get(key)
        if cached is None:
            return False
        try:
            current_mtime = Path(path).stat().st_mtime
        except OSError:
            return False
        return bool(cached["mtime"] == current_mtime)

    def get_cached_mtime(self, path: Path | str) -> float | None:
        """获取cache的 mtime (不读盘)。"""
        key = str(path)
        with self._lock:
            entry = self._cache.get(key)
            return entry["mtime"] if entry else None

    def invalidate(self, path: Path | str | None = None) -> None:
        """使cache失效。path=None 时清除全部cache。"""
        with self._lock:
            if path is None:
                self._cache.clear()
            else:
                self._cache.pop(str(path), None)

    def get_changed_files(self) -> list[str]:
        """return自上次read以来被修改的filelist (供 /doctor 用)。"""
        changed = []
        with self._lock:
            for key, entry in self._cache.items():
                try:
                    current_mtime = Path(key).stat().st_mtime
                    if current_mtime != entry["mtime"]:
                        changed.append(key)
                except OSError:
                    changed.append(key)  # 文件被删除也算变更
        return changed

    @property
    def size(self) -> int:
        """当前cache条目数。"""
        with self._lock:
            return len(self._cache)


# 全局singleton (process级)
_global_cache: FileStateCache | None = None
_global_lock = threading.Lock()


def get_file_state_cache() -> FileStateCache:
    """获取全局 FileStateCache singleton。"""
    global _global_cache
    if _global_cache is None:
        with _global_lock:
            if _global_cache is None:
                _global_cache = FileStateCache()
    return _global_cache
