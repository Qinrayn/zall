"""zall._util.jsonl — JSONL 版本头与坏行容错读取 (单一真相源)。

G12 (kimi context.jsonl `_` 特殊行 / wire.jsonl 首行 protocol_version 对标):
  timeline.jsonl 首行写 metadata 版本头 {"type":"metadata","version":1,...} —
  无版本头的旧文件视为 legacy 正常读 (向后兼容);
  读取循环坏行 skip 不再一坏全弃 (此前 _load_timeline_events 任一行
  JSONDecodeError 会整个返回 None, 丢掉全部可读事件)。

IPR constraints:
  IPR-0: tests/test_jsonl_header_invariants.py (含反例)
  IPR-3: 纯 stdlib
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

METADATA_TYPE = "metadata"
TIMELINE_VERSION = 1


def make_metadata(**fields: Any) -> dict[str, Any]:
    """构造版本头记录 (首行)。fields 覆盖默认键之外的补充信息。"""
    rec: dict[str, Any] = {"type": METADATA_TYPE, "version": TIMELINE_VERSION}
    rec.update(fields)
    return rec


def is_metadata(record: dict[str, Any]) -> bool:
    return isinstance(record, dict) and record.get("type") == METADATA_TYPE


def read_jsonl(
    path: str | Path,
    *,
    skip_metadata: bool = True,
) -> list[dict[str, Any]] | None:
    """读取 JSONL: 文件不存在/不可读 → None; 坏行 skip (不一坏全弃)。

    skip_metadata=True 时过滤版本头行 — 消费方拿到的纯是数据记录,
    legacy 文件 (无版本头) 行为完全一致。
    """
    p = Path(path)
    if not p.exists():
        return None
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return None
    records: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue  # 坏行 skip: 保住其余可读记录
        if not isinstance(rec, dict):
            continue
        if skip_metadata and is_metadata(rec):
            continue
        records.append(rec)
    return records


def read_metadata(path: str | Path) -> dict[str, Any] | None:
    """读首行版本头; 无版本头 (legacy) / 文件不存在 → None。"""
    p = Path(path)
    if not p.exists():
        return None
    try:
        with open(p, encoding="utf-8") as f:
            first = f.readline().strip()
    except OSError:
        return None
    if not first:
        return None
    try:
        rec = json.loads(first)
    except json.JSONDecodeError:
        return None
    return rec if is_metadata(rec) else None
