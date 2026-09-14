"""zall.extensions.science.state — 科研工作台会话状态 (Argus CLI 对象状态对标)。

选中模块 / 全局选项 / 每模块选项 / 收藏 / 最近使用 (deque 10) / profile。
持久化 ~/.zall/science_state.json — 跨会话记得你的工作台布局 (Argus 是
会话内的; zall 升级成持久, 科研是长周期活动)。

用 5 次自动建议收藏 (Argus _record_recent 思想): record_use() 返回 True 时
命令层弹一句建议, 用户说了算 (fav add), 不自动改状态。
"""

from __future__ import annotations

import json
from collections import deque
from pathlib import Path
from typing import Any

_STATE_PATH = Path.home() / ".zall" / "science_state.json"
_RECENT_MAX = 10
_FAV_SUGGEST_AFTER = 5


class ScienceState:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path or _STATE_PATH
        self.selected_id: str | None = None
        self.global_options: dict[str, str] = {}
        self.module_options: dict[str, dict[str, str]] = {}
        self.favorites: set[str] = set()
        self.recent: deque[str] = deque(maxlen=_RECENT_MAX)
        self.use_counts: dict[str, int] = {}
        self.profile: str = "deep"

    # ── 选项 (Argus _merge_options 层级: 全局 profile 底 → 全局手动 → 模块级最高) ──

    def merged_options(self, module_id: str) -> dict[str, str]:
        merged = dict(self.global_options)
        merged.update(self.module_options.get(module_id, {}))
        return {k: v for k, v in merged.items() if v}

    # ── 使用记录 / 收藏建议 ──

    def record_use(self, module_id: str) -> bool:
        """记录一次运行; 返回是否到达"建议收藏"阈值 (用 5 次)。"""
        try:
            self.recent.remove(module_id)
        except ValueError:
            pass
        self.recent.append(module_id)
        self.use_counts[module_id] = self.use_counts.get(module_id, 0) + 1
        return (
            self.use_counts[module_id] >= _FAV_SUGGEST_AFTER
            and module_id not in self.favorites
        )

    # ── 持久化 (坏文件降级为空状态, 不崩) ──

    def load(self) -> None:
        try:
            data: dict[str, Any] = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        self.selected_id = data.get("selected_id") or None
        self.global_options = {str(k): str(v) for k, v in (data.get("global_options") or {}).items()}
        self.module_options = {
            str(mid): {str(k): str(v) for k, v in (opts or {}).items()}
            for mid, opts in (data.get("module_options") or {}).items()
        }
        self.favorites = {str(f) for f in (data.get("favorites") or [])}
        self.recent = deque((str(r) for r in (data.get("recent") or [])), maxlen=_RECENT_MAX)
        self.use_counts = {str(k): int(v) for k, v in (data.get("use_counts") or {}).items()}
        self.profile = str(data.get("profile") or "deep")

    def save(self) -> None:
        data = {
            "selected_id": self.selected_id,
            "global_options": self.global_options,
            "module_options": self.module_options,
            "favorites": sorted(self.favorites, key=lambda x: (len(x), x)),
            "recent": list(self.recent),
            "use_counts": self.use_counts,
            "profile": self.profile,
        }
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass  # 状态写失败不打断工作流 (下一会话从头来)


_SESSION_STATE: ScienceState | None = None


def get_science_state() -> ScienceState:
    """进程内单例 (REPL/TUI 同一会话共享工作台状态)。"""
    global _SESSION_STATE
    if _SESSION_STATE is None:
        _SESSION_STATE = ScienceState()
        _SESSION_STATE.load()
    return _SESSION_STATE


def reset_science_state() -> None:
    global _SESSION_STATE
    _SESSION_STATE = None
