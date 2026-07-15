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
        """Happy path: EventType 有 12 种 (含 v0.0.5 anchor_ack + v0.0.10 context_compaction + v0.0.11 goal_downgrade + pr0_hallucination + v0.0.22 system_injection).

        Counterexample: 如果有人删了事件类型, 审计轨迹断 → fail.
        """
        types = {t for t in EventType}
        assert len(types) == 12
        assert EventType.ANCHOR_ACK in types
        assert EventType.CONTEXT_COMPACTION in types
        assert EventType.GOAL_DOWNGRADE in types
        assert EventType.PR0_HALLUCINATION in types
        assert EventType.MODEL_CALL in types
        assert EventType.TOOL_CALL_START in types
