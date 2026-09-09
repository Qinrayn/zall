"""zall.core.checkpoint — File-state checkpoint manager (DESIGN.md §4.2 checkpoint extension).

Corresponds to:
  §4.2   工具扩展: checkpoint system — 文件系统快照安全网
  §6.1   RunRecorder 集成: 每个 checkpoint 链接到 timeline 链式哈希
  §9.2.3 回退机制: `/revert` 恢复到指定 checkpoint

CheckpointManager 不依赖 git。它直接 copy 被追踪文件的快照到
.zall/checkpoints/<checkpoint_id>/ 目录下，不使用 git stash。

与 GitProtect 的关系:
  - GitProtect: git-native 安全层 (仅 git 仓库 + stash)
  - CheckpointManager: 通用文件系统快照 (任何目录)
  - 两者共存：GitProtect 在 loop 中做 Git stash; CheckpointManager 做文件快照
  - REPL 中 `revert` 优先用 CheckpointManager，回退 GitProtect

IPR constraints:
  IPR-0: invariant tests at tests/test_checkpoint_invariants.py
  IPR-1: corresponds to DESIGN.md §4.2 (工具扩展)
  IPR-3: only stdlib + hashlib, no model SDK
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from zall._util.logging import get_zall_logger as _get_zall_logger

_log = _get_zall_logger(__name__)

# B10: 去重 log — 同条 message 只打一次。
# M3 fix: 原为模块级全局 set (跨实例共享且只增不减 → 内存泄漏), 已改为
# CheckpointManager 实例级 (见 __init__ 的 self._log_spoken + self._log_once)。


# .zall 下 checkpoint 存储directory名
CHECKPOINT_DIR_NAME = "checkpoints"

# B11: 单个 snapshot 大小预算 — 超过则拒绝快照 (安全网不应吃光磁盘)。
# 注意不能做"部分快照": restore 时会静默丢文件, 故超预算整体拒绝。
MAX_SNAPSHOT_BYTES = 512 * 1024 * 1024  # 512 MB
# B11: 全部 checkpoint 总量/数量预算 — 超出从最旧开始修剪 (保留策略)。
MAX_TOTAL_SNAPSHOT_BYTES = 2 * 1024 * 1024 * 1024  # 2 GB
MAX_CHECKPOINT_COUNT = 50


@dataclass
class CheckpointEntry:
    """单条 checkpoint 元数据。"""
    checkpoint_id: str          # 唯一 ID (格式: "cp_<timestamp>_<sha256_prefix>")
    ts: float                   # Unix 时间戳 (秒)
    label: str                  # 用户可读标签 (如 "step_3_before_bash")
    snapshot_dir: str           # 快照目录路径 (相对于 .zall/checkpoints/)
    file_count: int             # 快照中包含的文件数
    total_bytes: int            # 快照总大小 (字节)
    prev_checkpoint_id: str | None = None  # 上一个 checkpoint ID (链式)
    tool_id: str = ""           # 触发 checkpoint 的工具 ID
    run_id: str = ""            # 关联的 run ID


class CheckpointManager:
    """filesystem checkpoint 管理器。

    在 .zall/checkpoints/ 下维护快照目录。
    每个 checkpoint 是一个目录, 包含:
      - meta.json: 元数据
      - files/ : 被追踪文件的副本 (保持目录结构)

    用法:
        mgr = CheckpointManager(project_root)
        cp = mgr.save_checkpoint(label="step_1", files={"src/main.py", "README.md"})
        mgr.restore_checkpoint(cp.checkpoint_id)
        checkpoints = mgr.list_checkpoints()
    """

    def __init__(self, project_root: str | Path | None = None) -> None:
        self._project_root = Path(project_root) if project_root else Path.cwd()
        self._cp_dir: Path | None = None
        # B11 fix: 根目录守卫 — home / 盘根不是项目根。
        # 在 home 下启动时全量快照会复制整个用户目录 (实测 60GB+ 磁盘爆满),
        # 此类根一律禁用 checkpoint (GitProtect 安全网仍在)。
        if self._is_unsafe_root(self._project_root):
            _log.warning(
                "checkpoint: project_root %s looks like home/drive root; "
                "checkpoints disabled to avoid snapshotting the whole disk",
                self._project_root,
            )
        else:
            self._cp_dir = self._project_root / ".zall" / CHECKPOINT_DIR_NAME
            try:
                self._cp_dir.mkdir(parents=True, exist_ok=True)
            except (OSError, PermissionError):
                # 非 git 仓库 / 无authoritydirectory → checkpoint 不可用
                self._cp_dir = None
        self._loaded: list[CheckpointEntry] = []
        self._dirty = False
        # Bug4 fix: 独立 load 标志 (原 `if self._loaded` 在空列表时 falsy → 每次重扫盘)
        self._load_done: bool = False
        # M3 fix: 去重日志集合改为实例级 (避免模块级全局 set 跨实例共享 + 泄漏)
        self._log_spoken: set[str] = set()

    def _log_once(self, msg: str) -> None:
        """同一条告警只打一次 (实例级去重, 避免刷屏, 且不跨实例泄漏)。"""
        if msg not in self._log_spoken:
            self._log_spoken.add(msg)
            _log.warning("checkpoint: %s", msg)

    @staticmethod
    def _is_unsafe_root(root: Path) -> bool:
        """B11: 判断 project_root 是否为 home / 盘根等非项目根。"""
        try:
            r = root.resolve()
        except OSError:
            r = root
        if r == r.parent:  # 盘根 (C:\ 或 /)
            return True
        try:
            home = Path.home().resolve()
        except (OSError, RuntimeError):
            return False
        # Windows 下比较带大小写归一化 — env 提供的路径常有盘符/case 差异
        # (如 c:\Users vs C:\Users), WindowsPath 比较虽已 normcase, 显式处理
        # 防止任何非 pathlib 归一化的构造路径漏判。
        if os.name == "nt":
            return os.path.normcase(str(r)) == os.path.normcase(str(home))
        return r == home

    # ── 公共property ──────────────────────────────────────────────

    @property
    def project_root(self) -> Path:
        """项目根directory (只读)。"""
        return self._project_root

    @property
    def cp_dir(self) -> Path | None:
        """checkpoint 存储directory (可能为 None)。"""
        return self._cp_dir

    # ── 公共 API ───────────────────────────────────────────────

    def save_checkpoint(
        self,
        *,
        label: str = "",
        files: set[str] | None = None,
        tool_id: str = "",
        run_id: str = "",
    ) -> CheckpointEntry | None:
        """savefilesystemsnapshot。

        files: 要追踪的文件路径集合 (相对于 project_root)。
               如果为 None, 不保存任何文件 (仅元数据 checkpoint)。
        label: 用户标签。
        tool_id: 触发 checkpoint 的工具 ID。
        run_id: 关联的 run ID。

        返回 CheckpointEntry, 或在无可追踪文件/超预算时返回 None。
        """
        fs_files = self._resolve_files(files)

        if not fs_files and not label:
            # 没有file也没有有意义标签 → 略过
            return None

        if self._cp_dir is None:
            return None

        # B11: 预算检查 — 先 stat 汇总, 超预算整体拒绝
        # (部分快照会让 restore 静默丢文件, 不可取)。
        sized: list[tuple[Path, int]] = []
        planned_bytes = 0
        for fpath in fs_files:
            try:
                sz = fpath.stat().st_size
            except OSError:
                continue
            sized.append((fpath, sz))
            planned_bytes += sz
        if planned_bytes > MAX_SNAPSHOT_BYTES:
            self._log_once(
                f"snapshot too large ({planned_bytes // (1024 * 1024)} MB > "
                f"{MAX_SNAPSHOT_BYTES // (1024 * 1024)} MB), refused"
            )
            return None

        # M3: ensure loaded from disk before accessing chain (cross-session)
        # B12: 必须在创建 _tmp_ 目录之前调用 — _ensure_loaded 的 GC 会回收
        # 所有 _tmp_* 目录, 若后调用会误删本次正在写入的快照。
        self._ensure_loaded()

        # 生成 checkpoint ID
        raw = f"{time.time()}_{label}_{tool_id}".encode()
        cid = "cp_" + hashlib.sha256(raw).hexdigest()[:16]

        ts = time.time()
        cp_dir = self._cp_dir / cid
        # B12 fix: 先写临时目录, meta.json 落盘后再 rename 提交。
        # 原实现 meta.json 最后写: 复制中途被 Ctrl+C/崩溃打断 → 留下无 meta 的
        # 孤儿快照, list/delete 都看不见, 磁盘永久泄漏 (实测积累 45GB+)。
        tmp_dir = self._cp_dir / f"_tmp_{cid}"
        files_dir = tmp_dir / "files"
        try:
            files_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            return None

        # 复制file
        file_entries: list[dict[str, Any]] = []
        total_bytes = 0
        try:
            for fpath, sz in sized:
                try:
                    rel = fpath.relative_to(self._project_root)
                except ValueError:
                    # B13: project_root 之外的文件不入快照 (restore 无法定位)
                    self._log_once(f"checkpoint: skip outside-root file {fpath}")
                    continue
                target = files_dir / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                try:
                    shutil.copy2(fpath, target)
                    total_bytes += sz
                    file_entries.append({
                        "path": str(rel),
                        "size": sz,
                    })
                except OSError as _cp_err:
                    # B10: 记录不可复制的file, 不静默skip
                    # 但 checkpoint 链不受损: skip单个file不影响其他file
                    self._log_once(f"checkpoint: cannot copy {rel}: {_cp_err}")

            if not file_entries:
                # 没有实际file被trace → 不要创建空的 checkpoint directory
                shutil.rmtree(tmp_dir, ignore_errors=True)
                return None

            # 获取上一个 checkpoint ID (链式)
            prev_id = self._loaded[-1].checkpoint_id if self._loaded else None

            entry = CheckpointEntry(
                checkpoint_id=cid,
                ts=ts,
                label=label or f"cp_{len(self._loaded)}",
                snapshot_dir=str(cp_dir.relative_to(self._project_root)),
                file_count=len(file_entries),
                total_bytes=total_bytes,
                prev_checkpoint_id=prev_id,
                tool_id=tool_id,
                run_id=run_id,
            )

            # write meta.json (仍在 tmp_dir 内, rename 前)
            meta = {
                "checkpoint_id": cid,
                "ts": ts,
                "label": entry.label,
                "snapshot_dir": entry.snapshot_dir,
                "file_count": entry.file_count,
                "total_bytes": total_bytes,
                "prev_checkpoint_id": prev_id,
                "tool_id": tool_id,
                "run_id": run_id,
                "files": file_entries,
            }
            (tmp_dir / "meta.json").write_text(
                json.dumps(meta, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            # B12: 原子提交 — rename 成功后快照才对 list/restore 可见
            tmp_dir.rename(cp_dir)
        except OSError:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            return None
        except BaseException:
            # KeyboardInterrupt / 进程被杀等: 清理半成品再传播 (B12: 不留孤儿)
            shutil.rmtree(tmp_dir, ignore_errors=True)
            raise

        self._loaded.append(entry)
        self._dirty = True
        # B11: 保留策略 — 总量/数量超预算时从最旧开始修剪
        self._prune_over_budget()
        return entry

    def restore_checkpoint(self, checkpoint_id: str) -> bool:
        """resume到指定 checkpoint。

        将快照中的文件复制回原位置。
        返回 True 表示恢复成功。
        """
        entry = self._find_entry(checkpoint_id)
        if entry is None:
            return False

        cp_dir = self._project_root / entry.snapshot_dir
        files_dir = cp_dir / "files"
        if not files_dir.is_dir():
            return False

        # Bug3 fix: _restore_tree 在 OSError 时会 raise 以触发回滚; 此处兑现 bool 契约
        # (磁盘满/只读时 /revert 返回 False 而非崩溃)。
        try:
            return self._restore_tree(files_dir, self._project_root)
        except OSError as e:
            self._log_once(f"restore failed (rolled back): {e}")
            return False

    def list_checkpoints(self) -> list[CheckpointEntry]:
        """列出所有 checkpoint (从最新到最旧)。"""
        self._ensure_loaded()
        return list(reversed(self._loaded))

    def get_checkpoint(self, checkpoint_id: str) -> CheckpointEntry | None:
        """按 ID find checkpoint。"""
        return self._find_entry(checkpoint_id)

    def delete_checkpoint(self, checkpoint_id: str) -> bool:
        """remove指定 checkpoint, 并更新后继者的链reference (M10)。"""
        entry = self._find_entry(checkpoint_id)
        if entry is None:
            return False

        # M10: find successor and update its prev_checkpoint_id before deletion
        for succ in self._loaded:
            if succ.prev_checkpoint_id == checkpoint_id:
                succ.prev_checkpoint_id = entry.prev_checkpoint_id
                # Persist the change to the successor's meta.json
                succ_cp_dir = self._project_root / succ.snapshot_dir
                meta_file = succ_cp_dir / "meta.json"
                if meta_file.is_file():
                    try:
                        meta = json.loads(meta_file.read_text(encoding="utf-8"))
                        meta["prev_checkpoint_id"] = entry.prev_checkpoint_id
                        meta_file.write_text(
                            json.dumps(meta, indent=2, ensure_ascii=False),
                            encoding="utf-8",
                        )
                    except (OSError, json.JSONDecodeError):
                        pass
                break

        cp_dir = self._project_root / entry.snapshot_dir
        shutil.rmtree(cp_dir, ignore_errors=True)

        self._loaded = [e for e in self._loaded if e.checkpoint_id != checkpoint_id]
        self._dirty = True
        return True

    def clear_all(self) -> int:
        """清空所有 checkpoint。returncleanup的 checkpoint 数。"""
        count = len(self._loaded)
        if self._cp_dir is None:
            self._loaded.clear()
            self._dirty = True
            return count
        shutil.rmtree(self._cp_dir, ignore_errors=True)
        self._cp_dir.mkdir(parents=True, exist_ok=True)
        self._loaded.clear()
        self._dirty = True
        return count

    def get_latest(self) -> CheckpointEntry | None:
        """获取最新 checkpoint。"""
        self._ensure_loaded()
        return self._loaded[-1] if self._loaded else None

    def get_chain_ids(self) -> list[str]:
        """按时间sequentialreturn所有 checkpoint ID (链式)。"""
        self._ensure_loaded()
        return [e.checkpoint_id for e in self._loaded]

    # ── 内部 ───────────────────────────────────────────────

    def _prune_over_budget(self) -> None:
        """B11: 保留策略 — 超出总量/数量预算时从最旧开始删, 至少保留最新 1 个。"""
        self._ensure_loaded()
        while len(self._loaded) > 1 and (
            len(self._loaded) > MAX_CHECKPOINT_COUNT
            or sum(e.total_bytes for e in self._loaded) > MAX_TOTAL_SNAPSHOT_BYTES
        ):
            oldest = self._loaded[0]
            if not self.delete_checkpoint(oldest.checkpoint_id):
                break

    def _resolve_files(self, files: set[str] | None) -> list[Path]:
        """将用户指定的pathparse为绝对 Path list。"""
        if files is None:
            return []

        resolved: list[Path] = []
        for f in files:
            p = Path(f)
            if not p.is_absolute():
                p = self._project_root / p
            if p.is_file():
                resolved.append(p)
        return resolved

    def _find_entry(self, checkpoint_id: str) -> CheckpointEntry | None:
        """按 ID find entry。"""
        self._ensure_loaded()
        for e in self._loaded:
            if e.checkpoint_id == checkpoint_id:
                return e
        return None

    def _ensure_loaded(self) -> None:
        """从diskload已存在的 checkpoint, 并 GC 孤儿快照 (B12)。"""
        if self._load_done and not self._dirty:
            return

        if self._cp_dir is None:
            self._loaded = []
            self._dirty = False
            self._load_done = True
            return

        if not self._cp_dir.is_dir():
            return

        entries: list[CheckpointEntry] = []
        for child in sorted(self._cp_dir.iterdir()):
            if not child.is_dir():
                continue
            # B12 GC: 中断残留的临时目录 → 直接回收
            if child.name.startswith("_tmp_"):
                shutil.rmtree(child, ignore_errors=True)
                continue
            meta_file = child / "meta.json"
            if not meta_file.is_file():
                # B12 GC: 旧版本残留的孤儿快照 (meta 未落盘, 不可恢复) → 回收磁盘。
                # 仅处理 cp_* 命名, 避免误删 _restore_backup 等工作目录。
                if child.name.startswith("cp_"):
                    self._log_once(f"GC orphan snapshot {child.name}")
                    shutil.rmtree(child, ignore_errors=True)
                continue
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
                entry = CheckpointEntry(
                    checkpoint_id=meta["checkpoint_id"],
                    ts=meta["ts"],
                    label=meta.get("label", ""),
                    snapshot_dir=meta.get("snapshot_dir", str(child.relative_to(self._project_root))),
                    file_count=meta.get("file_count", 0),
                    total_bytes=meta.get("total_bytes", 0),
                    prev_checkpoint_id=meta.get("prev_checkpoint_id"),
                    tool_id=meta.get("tool_id", ""),
                    run_id=meta.get("run_id", ""),
                )
                entries.append(entry)
            except (KeyError, json.JSONDecodeError):
                continue

        # 按时间sort
        entries.sort(key=lambda e: e.ts)
        self._loaded = entries
        self._dirty = False
        self._load_done = True

    def _restore_tree(self, src_dir: Path, dst_dir: Path) -> bool:
            """recursiveresumefile树, 含error回滚 (M9)。

            O7: 使用 _BackupRestore 上下文管理器, 分离备份/恢复/清理逻辑。
            """
            with _BackupRestore(dst_dir) as backup:
                try:
                    for item in src_dir.rglob("*"):
                        if item.is_file():
                            rel = item.relative_to(src_dir)
                            target = dst_dir / rel
                            target.parent.mkdir(parents=True, exist_ok=True)
                            # Backup existing file before overwriting
                            if target.exists():
                                backup.backup_file(rel, target)
                            shutil.copy2(item, target)
                    return True
                except OSError:
                    raise  # 让异常传播到 _BackupRestore.__exit__ 触发回滚 (fix B3)
            return False


class _BackupRestore:
    """fileresumeoperation的备份/回滚context manager (O7: 从 _restore_tree 提取)。

    用法:
        with _BackupRestore(dst_dir) as backup:
            backup.backup_file(rel, target)
            # ... 修改file ...
            # 成功 → 自动cleanup备份
            # exception → 自动回滚
    """

    def __init__(self, dst_dir: Path) -> None:
        self._dst_dir = dst_dir
        self._backup_dir = dst_dir / ".zall" / "checkpoints" / "_restore_backup"
        self._backup_files: list[Path] = []

    def __enter__(self) -> _BackupRestore:
        self._backup_dir.mkdir(parents=True, exist_ok=True)
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        if exc_type is not None:
            # Rollback: restore backed-up files to their original locations
            for bf in self._backup_files:
                try:
                    rel = bf.relative_to(self._backup_dir)
                    target = self._dst_dir / rel
                    shutil.copy2(bf, target)
                except OSError:
                    _log.warning(
                        "checkpoint rollback: failed to restore %s", bf, exc_info=True,
                    )
        # Cleanup backup dir regardless
        shutil.rmtree(self._backup_dir, ignore_errors=True)
        # Implicit None return — does not suppress exception (same as False)

    def backup_file(self, rel: Path, target: Path) -> None:
        """备份一个将被覆盖的file。"""
        backup = self._backup_dir / rel
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(target, backup)
        self._backup_files.append(backup)
