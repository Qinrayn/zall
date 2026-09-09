"""RunRecorder + TrustAnchor invariant test (DESIGN.md §6.1 + §6.5.2).

IPR-0: each test must contain a counterexample.
"""

from __future__ import annotations

import hashlib
import json

import pytest
from pydantic import ValidationError

from zall.core.verifiability import (
    AckEvent,
    EventType,
    RunRecorder,
    TimelineEvent,
    TrustAnchor,
    TrustAnchorInit,
)


# ──────────────────────────────────────────────────────────────────────────
# helpers
# ──────────────────────────────────────────────────────────────────────────


class _FakeAnchor:
    """TrustAnchor stub, for testing RunRecorder.anchor_to.

    不做真 ed25519 签名 (那是 TrustAnchor 实现层的职责),
    只returns一个固定签名的 AckEvent.
    """

    __test__ = False

    @property
    def anchor_id(self) -> str:
        return "fake_anchor"

    def write_run_tail(
        self, run_id: str, last_event_hash: str, ts: int
    ) -> AckEvent:
        return AckEvent(
            anchor_id=self.anchor_id,
            run_id=run_id,
            last_event_hash=last_event_hash,
            ts=ts,
            sig="fake_signature_hex",
        )


# ──────────────────────────────────────────────────────────────────────────
# §6.1 TimelineEvent invariants
# ──────────────────────────────────────────────────────────────────────────


class TestTimelineEventInvariants:
    """§6.1 TimelineEvent invariant."""

    def test_happy_path(self) -> None:
        """Happy path: valid event constructable."""
        e = TimelineEvent(
            event_id="e1",
            ts=1000,
            event_type=EventType.MODEL_CALL,
            payload={"model": "glm"},
        )
        assert e.event_id == "e1"

    def test_frozen_immutable(self) -> None:
        """Counterexample: construct后改 payload → must raise (append-only)."""
        e = TimelineEvent(
            event_id="e1", ts=1000, event_type=EventType.MODEL_CALL
        )
        with pytest.raises(ValidationError):
            e.payload = {"tampered": True}  # type: ignore[misc]

    def test_compute_hash_idempotent(self) -> None:
        """纯性: 同一 event 两次 compute_hash 结果相同."""
        e = TimelineEvent(
            event_id="e1", ts=1000, event_type=EventType.MODEL_CALL, payload={"x": 1}
        )
        assert e.compute_hash() == e.compute_hash()

    def test_compute_hash_changes_on_payload_change(self) -> None:
        """Counterexample: 不同 payload → 不同 hash (chain hash的前提).

        如果 compute_hash 对不同 payload returns相同 hash, chain hash无意义.
        """
        e1 = TimelineEvent(event_id="e1", ts=1000, event_type=EventType.MODEL_CALL, payload={"x": 1})
        e2 = TimelineEvent(event_id="e1", ts=1000, event_type=EventType.MODEL_CALL, payload={"x": 2})
        assert e1.compute_hash() != e2.compute_hash()

    def test_genesis_prev_hash(self) -> None:
        """Happy path: 首条 prev_hash = "0"*64 (genesis)."""
        e = TimelineEvent(event_id="e1", ts=1000, event_type=EventType.MODEL_CALL)
        assert e.prev_hash == "0" * 64

    def test_no_tool_history_marker(self) -> None:
        """TimelineEvent 是audit轨迹, not Context 的 tool 历史回灌源."""
        assert TimelineEvent.__no_tool_history__() is True


# ──────────────────────────────────────────────────────────────────────────
# §6.1 RunRecorder invariants
# ──────────────────────────────────────────────────────────────────────────


class TestRunRecorderInvariants:
    """§6.1 RunRecorder invariant."""

    def test_empty_recorder_tail_is_genesis(self) -> None:
        """Happy path: 空 recorder 的 tail_hash = "0"*64."""
        rec = RunRecorder("run_001")
        assert rec.tail_hash == "0" * 64

    def test_append_creates_linked_event(self) -> None:
        """Happy path: append 后 event.prev_hash == 前一条的 compute_hash."""
        rec = RunRecorder("run_001")
        e1 = rec.append("e1", 1000, EventType.MODEL_CALL, {"model": "glm"})
        assert e1.prev_hash == "0" * 64  # genesis

        e2 = rec.append("e2", 2000, EventType.TOOL_CALL_START, {"tool": "bash"})
        assert e2.prev_hash == e1.compute_hash()

    def test_verify_chain_passes_on_clean_chain(self) -> None:
        """Happy path: 未被篡改的链 verify_chain returns True."""
        rec = RunRecorder("run_001")
        rec.append("e1", 1000, EventType.MODEL_CALL)
        rec.append("e2", 2000, EventType.TOOL_CALL_START)
        rec.append("e3", 3000, EventType.TOOL_CALL_END)
        assert rec.verify_chain() is True

    def test_verify_chain_fails_on_tampered_event(self) -> None:
        """Counterexample: 篡改某条 event 的 payload → verify_chain False.

        §6.1 承诺: 篡改可发现.
        如果一个实现让篡改后的链仍 verify True, PR-0 被破坏 (审计无意义).
        """
        rec = RunRecorder("run_001")
        rec.append("e1", 1000, EventType.MODEL_CALL, {"x": 1})
        rec.append("e2", 2000, EventType.TOOL_CALL_START)

        # 篡改: directlyreplacememory中的 event (mock agent 改 timeline)
        tampered = TimelineEvent(
            event_id="e1",
            ts=1000,
            event_type=EventType.MODEL_CALL,
            payload={"x": 999},  # 篡改 payload
            prev_hash="0" * 64,
        )
        rec._events[0] = tampered  # type: ignore[private]
        assert rec.verify_chain() is False

    def test_events_readonly_view(self) -> None:
        """Happy path: events propertyreturns tuple (只读视图)."""
        rec = RunRecorder("run_001")
        rec.append("e1", 1000, EventType.MODEL_CALL)
        events = rec.events
        assert isinstance(events, tuple)
        assert len(events) == 1

    def test_anchor_to_writes_ack_event(self) -> None:
        """Happy path: anchor_to 把 ack 写回 timeline 作for ANCHOR_ACK event.

        §6.5.2.5: RunRecorder → anchor → ack → RunRecorder 闭环.
        """
        rec = RunRecorder("run_001")
        rec.append("e1", 1000, EventType.MODEL_CALL)
        rec.append("e2", 2000, EventType.TOOL_CALL_START)

        anchor = _FakeAnchor()
        ack_event = rec.anchor_to(anchor, ts=3000)
        assert ack_event is not None
        assert ack_event.event_type == EventType.ANCHOR_ACK
        assert ack_event.payload["anchor_id"] == "fake_anchor"
        assert ack_event.payload["sig"] == "fake_signature_hex"

        # ack 写回后链仍完整
        assert rec.verify_chain() is True

    def test_anchor_to_empty_recorder_returns_none(self) -> None:
        """Happy path: 空 recorder 的 anchor_to returns None (空链无意义)."""
        rec = RunRecorder("run_001")
        result = rec.anchor_to(_FakeAnchor(), ts=1000)
        assert result is None

    def test_tail_hash_after_anchor(self) -> None:
        """Happy path: anchor_to 后 tail_hash 是 ack event 的 hash."""
        rec = RunRecorder("run_001")
        rec.append("e1", 1000, EventType.MODEL_CALL)
        ack = rec.anchor_to(_FakeAnchor(), ts=2000)
        assert ack is not None
        assert rec.tail_hash == ack.compute_hash()


# ──────────────────────────────────────────────────────────────────────────
# §6.1 RunRecorder spill (M-fix: 内存窗口 + 磁盘全量)
# ──────────────────────────────────────────────────────────────────────────


class TestRunRecorderSpill:
    """RunRecorder spill 模式不变量 (内存有界, 链完整, 落盘可重建)。"""

    def test_memory_window_bounded_after_spill(self, tmp_path) -> None:
        """超过窗口上限 → 最旧事件落盘, 内存窗口有界 (反例: 无界增长)。"""
        rec = RunRecorder("spill_win", spill_dir=str(tmp_path),
                          max_memory_events=4)
        for i in range(20):
            rec.append(f"e{i}", 1000 + i, EventType.MODEL_CALL, {"i": i})
        assert len(rec._events) <= 4, "memory window must stay bounded"
        assert rec.spilled_count == 20 - len(rec._events)
        assert rec.spill_path is not None and rec.spill_path.exists()
        # len 是"盘 + 内存"总条数
        assert len(rec) == 20

    def test_full_reconstruction_matches_plain_recorder(self, tmp_path) -> None:
        """Happy path: iter_events/events 按序产出全部 20 条, 与无 spill 完全一致。"""
        seq = [(f"e{i}", 1000 + i, {"i": i}) for i in range(20)]
        plain = RunRecorder("plain_1")
        for eid, ts, payload in seq:
            plain.append(eid, ts, EventType.MODEL_CALL, payload)

        spilled = RunRecorder("spill_2", spill_dir=str(tmp_path),
                              max_memory_events=3)
        for eid, ts, payload in seq:
            spilled.append(eid, ts, EventType.MODEL_CALL, payload)

        got = [(e.event_id, e.payload) for e in spilled.iter_events()]
        want = [(e.event_id, e.payload) for e in plain.events]
        assert got == want, "spill reconstruction must equal no-spill sequence"
        assert len(spilled.events) == 20  # events property 物化全量

    def test_verify_chain_across_spill_boundary(self, tmp_path) -> None:
        """链跨盘/内存边界仍完整可验证 (反例: 边界断裂)。"""
        rec = RunRecorder("spill_chain", spill_dir=str(tmp_path),
                          max_memory_events=2)
        for i in range(10):
            rec.append(f"e{i}", 1000 + i, EventType.MODEL_CALL, {"i": i})
        assert rec.verify_chain() is True
        # 篡改盘上事件 (改首条 payload) → 整链验证必须失败 (链式不中断)
        path = rec.spill_path
        assert path is not None
        lines = path.read_text(encoding="utf-8").splitlines()
        import json as _json
        first = _json.loads(lines[0])
        first["payload"] = {"forged": True}
        path.write_text(_json.dumps(first, ensure_ascii=False) + "\n"
                        + "\n".join(lines[1:]), encoding="utf-8")
        assert rec.verify_chain() is False

    def test_spill_head_continuity_when_memory_emptied(self, tmp_path) -> None:
        """整窗都进盘后, 内存空 → tail_hash 仍指向 spill 尾 (链不丢头)。"""
        rec = RunRecorder("spill_tail", spill_dir=str(tmp_path),
                          max_memory_events=2)
        for i in range(15):
            rec.append(f"e{i}", 1000 + i, EventType.MODEL_CALL, {"i": i})
        last = rec.append("last", 9999, EventType.MODEL_CALL)
        assert last.prev_hash is not None
        # 内存窗 (2 条) + 盘 (14 条) — 全链可验证
        assert rec.verify_chain() is True
        assert rec.tail_hash == last.compute_hash()

    def test_save_then_continue_new_record_is_fresh(self, tmp_path) -> None:
        """会话保存 (关句柄 + 删 spill) 后, 同 run_id 新 recorder 是干净链 —
        不继承旧 spill 导致双链 (session-resume 语义回归)。"""
        sub = tmp_path / "spill_dir_save"
        rec = RunRecorder("js_run", spill_dir=str(sub), max_memory_events=2)
        for i in range(6):
            rec.append(f"e{i}", 1000 + i, EventType.MODEL_CALL)
        spilled_path = rec.spill_path
        assert spilled_path is not None and spilled_path.exists()
        rec.close()
        spilled_path.unlink(missing_ok=True)

        # session 保存后启动的新 recorder (同 run_id, 同 spill 目录)
        rec2 = RunRecorder("js_run", spill_dir=str(sub), max_memory_events=2)
        rec2.append("n0", 5000, EventType.MODEL_CALL)
        assert rec2.spilled_count == 0               # 不继承旧 spill
        assert [e.event_id for e in rec2.iter_events()] == ["n0"]
        assert rec2.verify_chain() is True           # genesis 起新链
        rec2.close()

    def test_len_reflects_total(self, tmp_path) -> None:
        """len() = 盘 + 内存总量, 不物化全量 (O(1))。"""
        rec = RunRecorder("spill_len", spill_dir=str(tmp_path),
                          max_memory_events=3)
        for i in range(7):
            rec.append(f"e{i}", 1000 + i, EventType.MODEL_CALL)
        assert len(rec) == 7
        assert rec.spilled_count > 0


# ──────────────────────────────────────────────────────────────────────────
# §6.5.2 TrustAnchor Protocol invariants
# ──────────────────────────────────────────────────────────────────────────


class TestTrustAnchorProtocolInvariants:
    """§6.5.2 TrustAnchor Protocol invariant."""

    def test_fake_anchor_is_trust_anchor(self) -> None:
        """Happy path: _FakeAnchor 满足 TrustAnchor Protocol."""
        assert isinstance(_FakeAnchor(), TrustAnchor)

    def test_bad_object_not_trust_anchor(self) -> None:
        """Counterexample: 缺 write_run_tail 的对象not TrustAnchor."""

        class _Bad:
            @property
            def anchor_id(self) -> str:
                return "x"

        assert not isinstance(_Bad(), TrustAnchor)


# ──────────────────────────────────────────────────────────────────────────
# §6.5.2 TrustAnchorInit invariants
# ──────────────────────────────────────────────────────────────────────────


class TestTrustAnchorInitInvariants:
    """§6.5.2.3 TrustAnchorInit invariant."""

    def test_happy_path(self) -> None:
        """Happy path: valid TrustAnchorInit constructable."""
        init = TrustAnchorInit(
            anchor_id="anchor_1",
            public_key_fp="abc123",
            ts_init=1000,
        )
        assert init.anchor_id == "anchor_1"

    def test_frozen_immutable(self) -> None:
        """Counterexample: construct后改 anchor_id → must raise (out-of-band 不可重写)."""
        init = TrustAnchorInit(
            anchor_id="anchor_1", public_key_fp="abc", ts_init=1000
        )
        with pytest.raises(ValidationError):
            init.anchor_id = "tampered"  # type: ignore[misc]


# ──────────────────────────────────────────────────────────────────────────
# §6.5.2 AckEvent invariants
# ──────────────────────────────────────────────────────────────────────────


class TestAckEventInvariants:
    """§6.5.2.4 AckEvent invariant."""

    def test_happy_path(self) -> None:
        """Happy path: valid AckEvent constructable."""
        ack = AckEvent(
            anchor_id="a1",
            run_id="r1",
            last_event_hash="0" * 64,
            ts=1000,
            sig="fake_sig",
        )
        assert ack.anchor_id == "a1"

    def test_frozen_immutable(self) -> None:
        """Counterexample: construct后改 sig → must raise (signimmutable)."""
        ack = AckEvent(
            anchor_id="a1", run_id="r1", last_event_hash="0" * 64, ts=1000, sig="sig"
        )
        with pytest.raises(ValidationError):
            ack.sig = "tampered"  # type: ignore[misc]

    def test_genesis_prev_anchor_hash(self) -> None:
        """Happy path: 首个 ack 的 prev_anchor_hash = "0"*64 (genesis)."""
        ack = AckEvent(
            anchor_id="a1", run_id="r1", last_event_hash="x" * 64, ts=1000, sig="s"
        )
        assert ack.prev_anchor_hash == "0" * 64


# ──────────────────────────────────────────────────────────────────────────
# §6.1 EventType invariants
# ──────────────────────────────────────────────────────────────────────────


class TestEventTypeInvariants:
    """§6.1 EventType invariant."""

    def test_eight_event_types(self) -> None:
        """Happy path: EventType 有 18 种 (含 v0.0.5 anchor_ack + v0.0.10 context_compaction + v0.0.11 goal_downgrade + pr0_hallucination + v0.0.22 system_injection + Phase 1 goal_statement + user_confirm + §12.3 E1.1 perception_anomaly + E4 user_interrupt + E6 chain_broken + context_rewind (kimi D-Mail 对标)).

        Counterexample: 如果有人删了事件类型, 审计轨迹断 -> fail.
        """
        types = {t for t in EventType}
        assert len(types) == 18
        assert EventType.ANCHOR_ACK in types
        assert EventType.CONTEXT_COMPACTION in types
        assert EventType.CONTEXT_REWIND in types
        assert EventType.GOAL_DOWNGRADE in types
        assert EventType.PR0_HALLUCINATION in types
        assert EventType.MODEL_CALL in types
        assert EventType.TOOL_CALL_START in types
        # Phase 1 (修裂缝): goal lifecycle events
        assert EventType.GOAL_STATEMENT in types
        assert EventType.USER_CONFIRM in types
        # §12.3 E1.1: perception anomaly
        assert EventType.PERCEPTION_ANOMALY in types
        # E4: user interrupt
        assert EventType.USER_INTERRUPT in types
        # E6: chain broken (tamper detection)
        assert EventType.CHAIN_BROKEN in types


# ──────────────────────────────────────────────────────────────────────────
# §6.5.2 FileTrustAnchor anchor-log trim (O(n) 行数缓存修复后的不变量)
# ──────────────────────────────────────────────────────────────────────────


class TestFileTrustAnchorTrim:
    """anchor 日志超过上限淘汰最旧 1/3, 链完整性不因 trim 断裂。"""

    def test_trim_keeps_log_bounded_and_chain_valid(self, tmp_path) -> None:
        """写入超过 _ANCHOR_LOG_MAX_ENTRIES 条 → 文件被裁, 链仍可验证。"""
        from zall.core.verifiability import FileTrustAnchor, _ANCHOR_LOG_MAX_ENTRIES

        anchor = FileTrustAnchor(work_dir=str(tmp_path))
        ts = 1_000_000_000
        for i in range(_ANCHOR_LOG_MAX_ENTRIES + 40):
            anchor.write_run_tail(f"run_{i:04d}", f"{i:064x}", ts + i)

        lines = [
            l for l in anchor._log_path.read_text(encoding="utf-8").splitlines()
            if l.strip()
        ]
        assert len(lines) < _ANCHOR_LOG_MAX_ENTRIES, "log must be trimmed"
        assert len(lines) >= _ANCHOR_LOG_MAX_ENTRIES * 2 // 3
        # 链完整性: 首条 prev_anchor_hash 继承被淘汰段 (verify_log_chain 必须 True)
        assert anchor.verify_log_chain() is True
        # 行数缓存与实际盘上一致 (O(n) 修复后缓存不该漂移)
        assert anchor._line_count == len(lines)
        # 继续追加仍正常 (trim 后新写入不破坏链)
        anchor.write_run_tail("run_final", "f" * 64, ts + 9999)
        assert anchor.verify_log_chain() is True


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
