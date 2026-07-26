"""Shared @-file-path completion (REPL + TUI): lazy workspace scan + ranked match.

Extracted so the inline REPL and the full-screen TUI use ONE implementation
(single source of truth). Typing `@src/lo` suggests workspace files ranked by
basename-prefix then path length.

IPR-3: stdlib only (os, re).
"""
from __future__ import annotations

import os
import re

# 末尾正在输入的 @token: 行首或空格后的 @, 其后无空白/@ (如 "explain @src/lo")
_FILE_QUERY_RE = re.compile(r"(?:^|\s)@([^\s@]*)$")

# 工作区文件/目录缓存: root -> [rel paths] (惰性建, 会话内复用; 大项目扫描昂贵)
_CACHE: dict[str, list[str]] = {}
_DIR_CACHE: dict[str, list[str]] = {}
_SCAN_LIMIT = 20000


def file_query(text: str) -> str | None:
    """提取末尾正在输入的 @token 查询; 无则 None。

    Counterexample: "mail a@b.com" (@ 前非空白) / "@a b" (@token 后有空格) → None。
    """
    m = _FILE_QUERY_RE.search(text or "")
    return m.group(1) if m is not None else None


def list_workspace_files(root: str | None = None) -> list[str]:
    """惰性扫描工作区文件 (跳过 noise 目录, 扫描上限, 结果按 root 缓存)。"""
    root = root or os.getcwd()
    cached = _CACHE.get(root)
    if cached is not None:
        return cached
    try:
        from zall._util import skip_noise_dirs
    except Exception:
        skip_noise_dirs = None  # type: ignore[assignment]
    files: list[str] = []
    scanned = 0
    for dirpath, dirnames, filenames in os.walk(root, topdown=True):
        if skip_noise_dirs is not None:
            skip_noise_dirs(dirnames)
        else:
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for fn in filenames:
            scanned += 1
            if scanned > _SCAN_LIMIT:
                _CACHE[root] = files
                return files
            try:
                rel = os.path.relpath(os.path.join(dirpath, fn), root).replace("\\", "/")
            except ValueError:
                # Windows 保留设备名 (nul/con/aux…) 或跨盘符路径 → relpath 报错, 跳过
                continue
            files.append(rel)
    _CACHE[root] = files
    return files


def list_workspace_dirs(root: str | None = None) -> list[str]:
    """惰性扫描工作区**目录** (跳过 noise 目录, 结果按 root 缓存)。用于 @dir 补全。"""
    root = root or os.getcwd()
    cached = _DIR_CACHE.get(root)
    if cached is not None:
        return cached
    try:
        from zall._util import skip_noise_dirs
    except Exception:
        skip_noise_dirs = None  # type: ignore[assignment]
    dirs: list[str] = []
    scanned = 0
    for dirpath, dirnames, filenames in os.walk(root, topdown=True):
        if skip_noise_dirs is not None:
            skip_noise_dirs(dirnames)
        else:
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for d in dirnames:
            scanned += 1
            if scanned > _SCAN_LIMIT:
                _DIR_CACHE[root] = dirs
                return dirs
            try:
                rel = os.path.relpath(os.path.join(dirpath, d), root).replace("\\", "/")
            except ValueError:
                continue
            dirs.append(rel)
    _DIR_CACHE[root] = dirs
    return dirs


def workspace_file_matches(
    query: str, limit: int = 8, root: str | None = None, include_dirs: bool = True,
) -> list[str]:
    """返回匹配 query 的文件/目录 (basename 前缀优先, 再按路径短优先); 内存过滤。

    include_dirs=True 时目录也参与补全 (带末尾 /), 便于 @src/ 引用目录清单。
    """
    q = (query or "").lower()
    cands: list[str] = [p for p in list_workspace_files(root) if (not q or q in p.lower())]
    if include_dirs:
        cands += [p + "/" for p in list_workspace_dirs(root) if (not q or q in p.lower())]

    def _rank(p: str) -> tuple[int, int]:
        base = p.rstrip("/").rsplit("/", 1)[-1].lower()
        return (0 if base.startswith(q) else 1, len(p))

    cands.sort(key=_rank)
    return cands[:limit]


def clear_cache() -> None:
    """清空工作区文件/目录缓存 (cwd 改变或文件增删后调用)。"""
    _CACHE.clear()
    _DIR_CACHE.clear()


# 所有 @path 引用 (行首/空格后的 @, 其后为非空白路径) — 用于提交时展开文件内容
_AT_REF_RE = re.compile(r"(?:^|\s)@([^\s@]+)")
# 展开上限: 单文件 / 总量 (防注入巨文件撞爆上下文)
_MAX_FILE_BYTES = 64 * 1024
_MAX_TOTAL_BYTES = 200 * 1024
_MAX_DIR_ENTRIES = 200


def _dir_listing(root: str, relpath: str, max_entries: int = _MAX_DIR_ENTRIES) -> list[str] | None:
    """一层目录清单 (子目录带 /, 跳过隐藏项, 上限截断)。读失败返回 None。"""
    full = os.path.join(root, relpath)
    try:
        names = sorted(os.listdir(full))
    except OSError:
        return None
    out: list[str] = []
    for name in names:
        if name.startswith("."):
            continue
        try:
            is_dir = os.path.isdir(os.path.join(full, name))
        except OSError:
            is_dir = False
        out.append(name + ("/" if is_dir else ""))
        if len(out) >= max_entries:
            out.append("... (truncated)")
            break
    return out


def expand_at_references(
    text: str,
    root: str | None = None,
    max_file_bytes: int = _MAX_FILE_BYTES,
    max_total_bytes: int = _MAX_TOTAL_BYTES,
) -> tuple[str, list[str]]:
    """将 text 中解析到真实文件/目录的 @path 引用展开为附加块 (Claude Code 式)。

    - @file → 注入文件内容 `<file>`; @dir/ → 注入一层目录清单 `<dir>`。
    - 只展开能在工作区解析到的**真实路径**; @foo 不存在则原样保留 (反例)。
    - 每文件 max_file_bytes / 总 max_total_bytes 上限, 超限截断并标注。
    - 二进制/读失败 → 标注跳过, 不崩溃。
    返回: (expanded_text, injected_refs)。无可展开引用时原文返回 + 空列表。
    """
    if not text or "@" not in text:
        return text, []
    root = root or os.getcwd()
    # 按出现顺序去重, 分类 file / dir (@src/ 末尾 / 归一化)
    refs: list[tuple[str, str]] = []
    seen: set[str] = set()
    real_root = os.path.realpath(root)
    for m in _AT_REF_RE.finditer(text):
        p = m.group(1).rstrip("/")
        if not p or p in seen:
            continue
        full = os.path.join(root, p)
        # 安全: 确保解析路径不超出工作区 (防 ../../ 路径穿越)
        try:
            real_full = os.path.realpath(full)
            if not real_full.startswith(real_root + os.sep) and real_full != real_root:
                continue
        except (OSError, ValueError):
            continue
        if os.path.isfile(full):
            seen.add(p)
            refs.append(("file", p))
        elif os.path.isdir(full):
            seen.add(p)
            refs.append(("dir", p))
    if not refs:
        return text, []

    blocks: list[str] = []
    injected: list[str] = []
    total = 0
    for kind, p in refs:
        if kind == "dir":
            listing = _dir_listing(root, p)
            if listing is None:
                continue
            body = "\n".join("  " + e for e in listing)
            blocks.append(f'<dir path="{p}/">\n{body}\n</dir>')
            injected.append(p + "/")
            continue
        # 敏感文件防护: @.env 等凭证文件不注入内容 (与 read_file 同源防线)
        from zall.safety.sensitive import is_sensitive_file
        if is_sensitive_file(p):
            blocks.append(f'<file path="{p}">[sensitive file skipped: '
                          'credentials / private key patterns are never injected]</file>')
            injected.append(p)
            continue
        try:
            with open(os.path.join(root, p), "rb") as f:
                raw = f.read(max_file_bytes + 1)
        except OSError:
            continue
        if b"\x00" in raw:
            blocks.append(f'<file path="{p}">[binary file skipped]</file>')
            injected.append(p)
            continue
        truncated = len(raw) > max_file_bytes
        content = raw[:max_file_bytes].decode("utf-8", errors="replace")
        if total + len(content) > max_total_bytes:
            blocks.append(f'<file path="{p}">[skipped: total injection budget exceeded]</file>')
            injected.append(p)
            break
        total += len(content)
        header = (f'<file path="{p}" note="truncated to {max_file_bytes} bytes">'
                  if truncated else f'<file path="{p}">')
        blocks.append(f"{header}\n{content}\n</file>")
        injected.append(p)

    if not blocks:
        return text, []
    expanded = text + "\n\n" + "\n\n".join(blocks)
    return expanded, injected
