"""zall._util.hash_utils - 真实文件 SHA-256 哈希工具 (E3 Science Kit 诚实化).

Part D (真溯源): ScienceProvenance 的四个 hash 字段此前是硬编码占位符
("sha256:cli-manual" / "sha256:agent"), 无任何代码对真实文件算哈希。本模块
提供真实文件哈希能力, 使实验溯源从"声明"变为"可验证事实"。

对应:
  docs/E3_SCIENCE_KIT.md §2.4  ScienceProvenance - protocol/data/code/env 完整血缘
  MASTER.md §10 I-10          负结果平等 (真实溯源同样适用于负结果)

IPR constraints:
  IPR-3: stdlib only (hashlib/pathlib), no model SDK. _util 是 stdlib-only 区。
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

# 哈希前缀, 与 ScienceProvenance 字段约定一致 (如 "sha256:<hex>")
_HASH_PREFIX = "sha256:"
_CHUNK_SIZE = 1 << 16  # 64 KiB 读块, 大文件不爆内存


def hash_file(path: str | Path) -> str:
    """计算单个文件的 SHA-256, 返回 "sha256:<hex>" 格式。

    文件不存在或不可读时抛 OSError (调用方决定降级策略)。
    """
    p = Path(path)
    h = hashlib.sha256()
    with p.open("rb") as f:
        while True:
            chunk = f.read(_CHUNK_SIZE)
            if not chunk:
                break
            h.update(chunk)
    return _HASH_PREFIX + h.hexdigest()


def hash_bytes(data: bytes) -> str:
    """计算字节内容的 SHA-256, 返回 "sha256:<hex>" 格式。"""
    return _HASH_PREFIX + hashlib.sha256(data).hexdigest()


def hash_files(paths: list[str | Path]) -> str:
    """计算多个文件的聚合 SHA-256 (顺序无关: 先排序再聚合)。

    聚合策略: 对每个文件取 (相对名, sha256) 后排序, 再对拼接串算总哈希。
    顺序无关保证: 同一组文件无论传入顺序如何, 聚合哈希一致。
    返回 "sha256:<hex>" 格式。
    """
    entries: list[tuple[str, str]] = []
    for path in paths:
        p = Path(path)
        digest = hash_file(p).removeprefix(_HASH_PREFIX)
        entries.append((p.name, digest))
    entries.sort(key=lambda e: e[0])
    joined = "\n".join(f"{name}\t{digest}" for name, digest in entries)
    return _HASH_PREFIX + hashlib.sha256(joined.encode("utf-8")).hexdigest()


def hash_dir(path: str | Path, *, pattern: str = "*") -> str:
    """计算目录下所有匹配文件的聚合 SHA-256 (环境快照用)。

    递归遍历目录, 对匹配 pattern 的文件聚合哈希 (排序保证顺序无关)。
    用于 ScienceProvenance.environment_hash (如 requirements.txt + python version)。
    返回 "sha256:<hex>" 格式。空目录返回对空串的哈希。
    """
    import fnmatch

    p = Path(path)
    if not p.is_dir():
        return hash_bytes(b"")
    entries: list[tuple[str, str]] = []
    for root, _dirs, files in os.walk(p):
        for fn in files:
            if not fnmatch.fnmatch(fn, pattern) and pattern != "*":
                continue
            fp = Path(root) / fn
            try:
                digest = hash_file(fp).removeprefix(_HASH_PREFIX)
                # 用相对路径作名, 保证目录迁移后哈希稳定
                rel = fp.relative_to(p).as_posix()
                entries.append((rel, digest))
            except OSError:
                continue  # 跳过不可读文件 (如权限/锁), 不让环境快照失败
    entries.sort(key=lambda e: e[0])
    joined = "\n".join(f"{name}\t{digest}" for name, digest in entries)
    return _HASH_PREFIX + hashlib.sha256(joined.encode("utf-8")).hexdigest()


def environment_hash(extra: str = "") -> str:
    """计算运行环境快照哈希 (python 版本 + 关键库版本 + 可选附加串)。

    用于 ScienceProvenance.environment_hash: 捕获 (python 版本, sys.platform,
    可选的 requirements 摘要), 使实验环境可溯源。
    返回 "sha256:<hex>" 格式。
    """
    import sys
    parts = [
        f"python={sys.version.split()[0]}",
        f"platform={sys.platform}",
        f"executable={sys.executable}",
    ]
    if extra:
        parts.append(extra)
    joined = "\n".join(parts)
    return _HASH_PREFIX + hashlib.sha256(joined.encode("utf-8")).hexdigest()
