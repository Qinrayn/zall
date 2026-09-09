"""zall.core.loop_checkpoint — 文件级 checkpoint 追踪 (从 loop.py 抽取, 单一职责)。

Corresponds to:
  §7 双安全网之 file-based 侧 (CheckpointManager)。git-native 侧 (GitProtect)
     仍在 AgentLoop._maybe_checkpoint 编排。
  B1/O3: 增量追踪 + 全量扫描缓存 — 避免每次写工具调用都 os.walk 整个项目。

设计:
  无状态自由函数, 接收 loop 实例 (与 executor.py / context_manager.py 协作者模式
  一致)。AgentLoop 保留 _maybe_checkpoint (git + 委托) 与薄委托方法, 兼容既有
  调用与既有测试 (test_plugin_safety 会把 loop._maybe_checkpoint 替换为 MagicMock)。

  此前这些逻辑内联在 AgentLoop (~90 行), 为给 loop.py "god class" 瘦身
  (架构评估 P0-item2 / 推荐 C) 抽取为独立模块。

不变量:
  - 静默降级: 安全网故障 (checkpoint 异常) 不得改变 RunEgress (IPR-0 反例),
    但须可观测 — 记 warning, 不静默 pass。
  - 敏感文件排除: .env / *.key / *secret* 等永不进快照 (secret leak 防护 S1)。
  - 确定性: 扫描结果排序后返回 (可复现)。

IPR constraints:
  IPR-0: invariant tests at tests/test_checkpoint_invariants.py
  IPR-3: stdlib only (os, fnmatch), 不 import 模型 SDK
"""

from __future__ import annotations

import fnmatch
import os
from typing import Any

from zall._util import is_noise, skip_noise_dirs
from zall._util.logging import get_zall_logger as _get_zall_logger
from pathlib import PurePath

_log = _get_zall_logger(__name__)


# 追踪的文件扩展名 (源码 / 文档 / 配置)。变更此表须同步 checkpoint 相关测试。
CHECKPOINT_TRACKED_EXTS: frozenset[str] = frozenset({
    ".py", ".js", ".ts", ".md", ".toml", ".yaml", ".yml",
    ".json", ".css", ".html", ".rs", ".go", ".java",
})

# S1: 排除敏感文件模式 (secret leak 防护)。fnmatch 同时匹配 .env 与 key.pem。
CHECKPOINT_EXCLUDE_PATTERNS: tuple[str, ...] = (
    ".env", ".env.*", "*.pem", "*.key", "*.cert",
    "*secret*", "*password*", "*credential*",
    "id_rsa", "id_ed25519", "*.pub",
)


def is_checkpoint_trackable(rel_path: str) -> bool:
    """S1 fix: 单一路径是否可入快照 (扩展名白名单 + 敏感模式 + noise 目录)。

    此前排除逻辑只存在于 scan_tracked_files 全量扫描路径; write_file/edit_file
    的快速路径直接返回工具参数中的 path, 完全绕过 S1 排除 → 编辑 .env 会把
    secret 原样复制进 .zall/checkpoints/ (secret leak)。现统一收口到本函数。
    """
    norm = rel_path.replace("\\", "/")
    ext = os.path.splitext(norm)[1].lower()
    if ext not in CHECKPOINT_TRACKED_EXTS:
        return False
    base = norm.rsplit("/", 1)[-1]
    # 同时匹配 basename 与完整相对路径 (".env" 模式须命中 "sub/.env")
    for pat in CHECKPOINT_EXCLUDE_PATTERNS:
        if fnmatch.fnmatch(base, pat) or fnmatch.fnmatch(norm, pat):
            return False
    # noise 目录内文件 (node_modules/.zall 等) 不追踪
    if is_noise(PurePath(norm)):
        return False
    return True


def maybe_checkpoint_file(
    loop: Any, tool_id: str, action_args: dict[str, Any] | None = None
) -> None:
    """写操作后自动文件系统快照 (CheckpointManager, v0.1.0)。

    B1 优化:
      - 首次调用: 全量 os.walk 扫描, 缓存结果到 loop._cached_tracked_files
      - 后续调用: 从工具参数提取文件路径, 仅追踪本次修改的文件
      - bash 等无法获知具体文件的工具: 使用缓存的全量列表

    O3 增量 cache 策略:
      - write_file/edit_file/batch_edit (已知路径): 直接加入 cache, 避免全量扫描
      - bash (未知路径): 使 cache 失效, 下次全量扫描

    静默降级: 安全网故障不得改变 RunEgress (IPR-0 反例), 但记 warning 保持可观测。
    """
    if loop._checkpoint_mgr is None:
        return
    try:
        tracked = get_checkpoint_files(loop, tool_id, action_args)
        if not tracked:
            return

        loop._checkpoint_mgr.save_checkpoint(
            label=f"step_{loop._step_count}_{tool_id}",
            files=tracked,
            tool_id=tool_id,
            run_id=loop._recorder.run_id,
        )
        # O3: 增量 cache 策略 — 已知路径直接加入, 未知路径才全量扫描
        if (tool_id in ("write_file", "edit_file", "batch_edit")
                and loop._cached_tracked_files is not None):
            # 已知路径: 增量加入 cache, 避免全量 os.walk
            loop._cached_tracked_files.update(tracked)
        else:
            # bash 等未知路径: 使 cache 失效, 下次重新扫描
            loop._cached_tracked_files = None
    except Exception as _cp_err:
        # 安全网故障不得改变 RunEgress, 但须可观测 (不静默)
        _log.warning("file checkpoint failed (safety net degraded): %s", _cp_err)


def get_checkpoint_files(
    loop: Any, tool_id: str, action_args: dict[str, Any] | None = None
) -> set[str]:
    """获取本次 checkpoint 需要追踪的文件列表。

    B1 优化:
      - write_file/edit_file: 从工具参数提取路径, 增量追踪 (O(1))
      - batch_edit: 从 edits 列表提取每个 path
      - bash: 使用缓存的全量扫描结果 (O(n) 仅首次)
    """
    # 从工具参数提取 path (write_file/edit_file 等明确 path 的工具)
    if action_args:
        path = action_args.get("path") or action_args.get("file_path") or ""
        if path:
            norm = path.replace("\\", "/")
            # S1 fix: 快速路径同样过 is_checkpoint_trackable (敏感文件不入快照);
            # 不可回退全量扫描 — 本次只改了这个文件, 快照其他文件无意义。
            return {norm} if is_checkpoint_trackable(norm) else set()
        # batch_edit: edits list 含多个 path
        if tool_id == "batch_edit":
            edits = action_args.get("edits", [])
            if edits and isinstance(edits, list):
                paths = set()
                for ed in edits:
                    p = ed.get("path", "") if isinstance(ed, dict) else ""
                    if p:
                        norm = p.replace("\\", "/")
                        if is_checkpoint_trackable(norm):  # S1 fix
                            paths.add(norm)
                if paths:
                    return paths

    # cache 未初始化 → 全量扫描 (仅首次)
    if loop._cached_tracked_files is None:
        loop._cached_tracked_files = scan_tracked_files(loop)

    return loop._cached_tracked_files or set()


def scan_tracked_files(loop: Any) -> set[str]:
    """全量扫描项目目录, 返回追踪文件集合 (仅首次调用, 结果缓存到 loop 实例)。"""
    if loop._checkpoint_mgr is None:
        return set()
    root = loop._checkpoint_mgr.project_root
    candidates: list[str] = []
    if root.is_dir():
        for dirpath, dirnames, filenames in os.walk(str(root), topdown=True):
            # 提前过滤 noise 目录 (不遍历!)
            skip_noise_dirs(dirnames)
            rel_base = os.path.relpath(dirpath, str(root))
            for fn in filenames:
                rel = os.path.join(rel_base, fn) if rel_base != "." else fn
                rel_norm = rel.replace("\\", "/")
                # S1: 扩展名 + 敏感模式统一走 is_checkpoint_trackable
                if is_checkpoint_trackable(rel_norm):
                    candidates.append(rel_norm)

    # 确定性排序后返回完整集合 (不硬限文件数, fix B9)
    candidates.sort()
    return set(candidates)
