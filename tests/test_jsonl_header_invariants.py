"""G12 timeline 版本头 + 坏行容错不变量测试 (IPR-0, 含反例).

不变量:
  I-JH-1  make_metadata/is_metadata 往返; 普通事件行不是 metadata (反例)
  I-JH-2  read_jsonl 默认过滤版本头; skip_metadata=False 保留 (反例)
  I-JH-3  坏行 skip 保住其余记录; 全坏 → 空 → 消费方 None (反例)
  I-JH-4  legacy 无头文件行为完全一致 (向后兼容)
  I-JH-5  read_metadata: 有头返回含 version; legacy → None (反例)
  I-JH-6  端到端: 带头 timeline 经 _load_timeline_events 与
          core/eval.load_timeline 都拿到纯事件
"""

from __future__ import annotations

import json
from pathlib import Path

from zall._util.jsonl import (
    TIMELINE_VERSION,
    is_metadata,
    make_metadata,
    read_jsonl,
    read_metadata,
)

_EV1 = {"event_id": "e1", "ts": 1, "event_type": "goal_statement", "payload": {}}
_EV2 = {"event_id": "e2", "ts": 2, "event_type": "anchor_ack", "payload": {}}


def _write(tmp_path: Path, lines: list[str]) -> Path:
    p = tmp_path / "timeline.jsonl"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


# ── I-JH-1 metadata 构造/判定 ──


def test_metadata_roundtrip():
    rec = make_metadata(run_id="r1")
    assert is_metadata(rec)
    assert rec["version"] == TIMELINE_VERSION
    assert rec["run_id"] == "r1"


def test_event_is_not_metadata_counterexample():
    """反例: 普通事件行不被误判为 metadata。"""
    assert not is_metadata(_EV1)


# ── I-JH-2 版本头过滤 ──


def test_read_jsonl_filters_header(tmp_path):
    p = _write(tmp_path, [json.dumps(make_metadata()), json.dumps(_EV1), json.dumps(_EV2)])
    recs = read_jsonl(p)
    assert [r["event_id"] for r in recs] == ["e1", "e2"]


def test_read_jsonl_keeps_header_when_asked_counterexample(tmp_path):
    """反例: skip_metadata=False 保留版本头。"""
    p = _write(tmp_path, [json.dumps(make_metadata()), json.dumps(_EV1)])
    recs = read_jsonl(p, skip_metadata=False)
    assert len(recs) == 2 and is_metadata(recs[0])


# ── I-JH-3 坏行容错 ──


def test_bad_line_skipped_keeps_rest(tmp_path):
    p = _write(tmp_path, [json.dumps(_EV1), "{corrupted!!", json.dumps(_EV2)])
    recs = read_jsonl(p)
    assert [r["event_id"] for r in recs] == ["e1", "e2"]


def test_all_bad_lines_yield_empty_counterexample(tmp_path):
    """反例: 全坏文件 → 空列表 (消费方按'无事件'处理)。"""
    p = _write(tmp_path, ["not json", "{broken"])
    assert read_jsonl(p) == []


def test_missing_file_returns_none(tmp_path):
    assert read_jsonl(tmp_path / "nope.jsonl") is None


# ── I-JH-4 legacy 兼容 ──


def test_legacy_file_without_header_unchanged(tmp_path):
    p = _write(tmp_path, [json.dumps(_EV1), json.dumps(_EV2)])
    recs = read_jsonl(p)
    assert [r["event_id"] for r in recs] == ["e1", "e2"]


# ── I-JH-5 read_metadata ──


def test_read_metadata_present(tmp_path):
    p = _write(tmp_path, [json.dumps(make_metadata(run_id="r9")), json.dumps(_EV1)])
    meta = read_metadata(p)
    assert meta and meta["version"] == TIMELINE_VERSION and meta["run_id"] == "r9"


def test_read_metadata_legacy_none_counterexample(tmp_path):
    """反例: legacy 无头文件 → None (不误把首个事件当元数据)。"""
    p = _write(tmp_path, [json.dumps(_EV1)])
    assert read_metadata(p) is None


# ── I-JH-6 端到端消费方 ──


def test_load_timeline_events_filters_header_and_bad_lines(tmp_path):
    from zall.cli.commands.system import _load_timeline_events

    d = tmp_path / "sess"
    d.mkdir()
    _write(d, [json.dumps(make_metadata()), json.dumps(_EV1), "{bad", json.dumps(_EV2)])
    events = _load_timeline_events(d)
    assert [e["event_id"] for e in events] == ["e1", "e2"]


def test_core_eval_load_timeline_skips_header(tmp_path):
    from zall.core.eval import load_timeline

    d = tmp_path / "sess"
    d.mkdir()
    _write(d, [json.dumps(make_metadata()), json.dumps(_EV1)])
    events = load_timeline(d)
    assert events is not None and len(events) == 1
    assert events[0].event_id == "e1"
