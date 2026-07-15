"""zall.tools.batch_edit — 多file批量edittool (ACI design).

Multi-file batch editing capability:
  - 一次调用编辑多个文件
  - 原子性: 全部成功或全部回滚
  - 预检: 先读所有文件检查 old_string 唯一性, 再执行
  - 返回统一 diff 摘要

Design:
  - 每个 edit 与 edit_file 共享相同的匹配逻辑
  - 两步提交: validate → apply
  - 失败时提供各文件的具体错误信息
  - Windows 跨设备回退: os.replace 失败时走 shutil.move

IPR constraints:
  IPR-0: invariant tests at tests/test_batch_edit_invariants.py
  IPR-1: corresponds to DESIGN.md §4.2 (tool layer)
  IPR-3: only stdlib, no model SDK
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

from zall.core.tool import Tool, ToolResult
from zall._util.path import resolve_path
from zall.tools._diff import unified_diff as _unified_diff


_MAX_EDITS = 50  # 单次最大编辑数 (防 context 膨胀)


class BatchEditTool:
    """多file批量edittool (ACI design).

    接收一个 edits 列表, 每个 edit 含 {path, old_string, new_string}.
    分两阶段执行:
      1. validate: 读取所有文件, 检查每个 old_string 唯一匹配
      2. apply: 全部验证通过后一次性写入

    如果任一 edit 验证失败, 全部不执行, 返回详细错误.
    """

    __test__ = False

    @property
    def tool_id(self) -> str:
        return "batch_edit"

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "batch_edit",
                "description": (
                    "Edit multiple files in one call. Each edit specifies a file path, "
                    "the exact string to replace (must be unique in the file), "
                    "and the replacement string. All edits are validated before any write: "
                    "if any edit fails validation, no files are modified. "
                    "Returns a unified diff summary with per-file status. "
                    "Prefer this over individual edit_file calls when making multiple related changes."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "edits": {
                            "type": "array",
                            "description": (
                                "List of edits to perform. "
                                "Each edit must have a unique path, old_string, and new_string. "
                                "All edits are validated before any write."
                            ),
                            "items": {
                                "type": "object",
                                "properties": {
                                    "path": {
                                        "type": "string",
                                        "description": "File path to edit (absolute or relative to cwd)",
                                    },
                                    "old_string": {
                                        "type": "string",
                                        "description": (
                                            "The exact string to replace. Must match exactly once "
                                            "in the file, including whitespace and indentation."
                                        ),
                                    },
                                    "new_string": {
                                        "type": "string",
                                        "description": "The replacement string",
                                    },
                                },
                                "required": ["path", "old_string", "new_string"],
                            },
                            "minItems": 1,
                            "maxItems": _MAX_EDITS,
                        },
                    },
                    "required": ["edits"],
                },
            },
        }

    def execute(self, args: dict[str, Any]) -> ToolResult:
        edits = args.get("edits", [])
        if not edits or not isinstance(edits, list):
            return ToolResult(
                success=False,
                output="[ERROR: edits list is required]",
                error="edits required",
            )

        if len(edits) > _MAX_EDITS:
            return ToolResult(
                success=False,
                output=f"[ERROR: too many edits ({len(edits)} > {_MAX_EDITS} max)]",
                error="too many edits",
            )

        # ── Phase 1: Validate all edits ──
        resolved: list[dict[str, Any]] = []
        errors: list[str] = []

        for i, edit in enumerate(edits):
            path_str = edit.get("path", "")
            old = edit.get("old_string", "")
            new = edit.get("new_string", "")

            # 基本verify
            if not path_str:
                errors.append(f"  [{i}] path is required")
                continue
            if not old:
                errors.append(f"  [{i}] old_string is required for {path_str}")
                continue

            path = resolve_path(path_str)

            # readfile (使用共享tool)
            try:
                from zall._util import read_text_file
                content = read_text_file(path)
            except FileNotFoundError:
                errors.append(f"  [{i}] file not found: {path}")
                continue
            except IsADirectoryError:
                errors.append(f"  [{i}] not a file: {path}")
                continue
            except OSError as e:
                errors.append(f"  [{i}] cannot read {path}: {e}")
                continue

            # check唯一匹配
            count = content.count(old)
            if count == 0:
                errors.append(
                    f"  [{i}] old_string not found in {path}\n"
                    f"    The file has {len(content)} characters. "
                    "Make sure the old_string exactly matches."
                )
                continue
            if count > 1:
                # 列出匹配位置
                lines = content.split("\n")
                locations = []
                for ln, line in enumerate(lines, 1):
                    if old in line:
                        locations.append(f"      Line {ln}: {line.strip()[:80]}")
                errors.append(
                    f"  [{i}] old_string matched {count} times in {path} "
                    "(must be unique):\n" + "\n".join(locations[:10])
                )
                continue

            # 通过validate
            resolved.append({
                "path": path,
                "old": old,
                "new": new,
                "content": content,
                "index": i,
            })

        # 如果有validateerror, 全部不execute
        if errors:
            return ToolResult(
                success=False,
                output="[ERROR] Batch edit validation failed — no files were modified:\n"
                + "\n".join(errors),
                error="validation failed",
                artifacts={
                    "validated": len(resolved),
                    "failed": len(errors),
                    "errors": errors,
                },
            )

        # ── Phase 2: Apply all edits atomically ──
        # v0.0.6 fix (H10): 先全部write临时file, 再原子replace, 保证 all-or-nothing
        results: list[dict[str, Any]] = []
        all_success = True
        tmp_files: list[tuple[Path, Path]] = []  # (tmp_path, target_path)

        try:
            for r in resolved:
                path = r["path"]
                old = r["old"]
                new = r["new"]
                content = r["content"]

                new_content = content.replace(old, new, 1)
                # v2 fix: 使用 uuid 唯一临时file名, 避免concurrentwrite竞态
                import uuid as _uuid
                tmp = path.parent / f".zall_tmp_{_uuid.uuid4().hex[:8]}"
                tmp.write_text(new_content, encoding="utf-8")
                tmp_files.append((tmp, path))

                old_lines = old.count("\n") + 1
                new_lines = new.count("\n") + 1
                diff = _unified_diff(old, new)
                results.append({
                    "path": str(path),
                    "status": "ok",
                    "old_lines": old_lines,
                    "new_lines": new_lines,
                    "diff": diff,
                })

            # 全部write成功 → 原子replace
            replaced: list[tuple[Path, Path, str | None]] = []  # (tmp, target, original_content)
            for tmp, target in tmp_files:
                # 备份原filecontent (用于resume)
                orig_content = target.read_text(encoding="utf-8") if target.exists() else None
                try:
                    os.replace(str(tmp), str(target))
                    replaced.append((tmp, target, orig_content))
                except OSError:
                    # Windows 跨设备fallback: os.replace 在不同filesystem间失败
                    # (如 OneDrive 挂载点), fallback到 shutil.move
                    try:
                        shutil.move(str(tmp), str(target))
                        replaced.append((tmp, target, orig_content))
                    except OSError:
                        # os.replace 和 shutil.move 都失败 → resume已replace的file
                        _recovery_failed = []
                        for _tmp, _target, _orig in reversed(replaced):
                            try:
                                if _orig is not None:
                                    _target.write_text(_orig, encoding="utf-8")
                                else:
                                    _target.unlink(missing_ok=True)
                            except Exception:
                                _recovery_failed.append(str(_target))
                        # 在 raise 之前如果有resume失败, 附加到exceptionmessage
                        if _recovery_failed:
                            raise OSError(
                                f"batch_edit failed and recovery also failed for: {_recovery_failed}"
                            ) from None
                        raise

        except OSError as e:
            # write或replace失败 → 原子性保证: 回滚 + cleanup
            all_success = False
            results.append({
                "path": str(tmp_files[-1][1]) if tmp_files else "?",
                "status": "error",
                "error": str(e),
            })
            # cleanup临时file (仅cleanup未被成功replace的, 已replace的临时file已被 os.replace 移走)
            replaced_tmps = {str(t) for t, _, _ in replaced} if replaced else set()
            for tmp, _ in tmp_files:
                if str(tmp) not in replaced_tmps:
                    try:
                        tmp.unlink()
                    except Exception:
                        pass

        # buildoutputdigest
        ok_count = sum(1 for r in results if r["status"] == "ok")
        err_count = sum(1 for r in results if r["status"] == "error")
        summary_lines = [f"Batch edit: {ok_count} file(s) edited"]
        if err_count:
            summary_lines.append(f"  {err_count} file(s) failed:")
        for r in results:
            if r["status"] == "ok":
                summary_lines.append(
                    f"  ✓ {r['path']}: {r['old_lines']} → {r['new_lines']} lines"
                )
            else:
                summary_lines.append(f"  ✗ {r['path']}: {r['error']}")

        # 收集 diff (truncate, 防 context 膨胀)
        all_diffs = {}
        for r in results:
            if r["status"] == "ok" and r.get("diff"):
                all_diffs[r["path"]] = r["diff"]

        return ToolResult(
            success=all_success,
            output="\n".join(summary_lines),
            artifacts={
                "total": len(results),
                "ok": ok_count,
                "failed": err_count,
                "results": results,
                "diffs": all_diffs,
            },
        )


# _unified_diff now imported from zall.tools._diff (v0.1.1 refactor R2)