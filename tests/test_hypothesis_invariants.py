"""Hypothesis invariant test (E3_SCIENCE_KIT.md §2.1).

IPR-0: each test must contain a counterexample —— not happy path, but construct violations
that should cause the test to fail.
Counterexample摘要见 tests/INVARIANTS.md.

Corresponds to:
  H-1: lock() 后 claim/prediction 不可变
  H-2: revise() 产生新 UUID + version+1 + revised_from 指向原
  H-3: falsify() 要求 evidence_against 非空且 falsified_by 在其中
  H-4: confidence 必须 [0,1]
"""

from __future__ import annotations

import time
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from zall.core.hypothesis import Hypothesis, HypothesisStatus


# ──────────────────────────────────────────────────────────────────────────
# helpers
# ──────────────────────────────────────────────────────────────────────────


def _make_hypothesis(
    claim: str = "Spectral 嵌入的 G-F Score 显著高于随机",
    prediction: str = "G-F > 0.128, p < 0.05",
    confidence: float = 0.5,
    status: HypothesisStatus = HypothesisStatus.PROPOSED,
    evidence_against: list[UUID] | None = None,
) -> Hypothesis:
    """快捷构造一个有效的 Hypothesis 实例."""
    return Hypothesis(
        id=uuid4(),
        claim=claim,
        prediction=prediction,
        confidence=confidence,
        evidence_for=[],
        evidence_against=evidence_against or [],
        status=status,
        version=1,
        revised_from=None,
        falsified_by=None,
        created_by="test_agent",
        created_at=int(time.time()),
        immutable_after_lock=False,
    )


# ──────────────────────────────────────────────────────────────────────────
# H-1: lock() 后 claim/prediction 不可变
# ──────────────────────────────────────────────────────────────────────────


class TestH1LockImmutability:
    """H-1: Hypothesis.lock() 后 claim/prediction 不可修改."""

    def test_lock_makes_claim_immutable(self) -> None:
        """H-1 positive: lock() 后修改 claim → must raise ValueError.

        lock() 设 immutable_after_lock=True, 之后任何 claim 修改须拒绝.
        """
        h = _make_hypothesis()
        h.lock()
        with pytest.raises(ValueError, match="Hypothesis is locked"):
            h.claim = "篡改后的 claim"  # type: ignore[misc]

    def test_lock_makes_prediction_immutable(self) -> None:
        """H-1 positive: lock() 后修改 prediction → must raise ValueError."""
        h = _make_hypothesis()
        h.lock()
        with pytest.raises(ValueError, match="Hypothesis is locked"):
            h.prediction = "篡改后的 prediction"  # type: ignore[misc]

    def test_counterexample_unlocked_can_modify_claim(self) -> None:
        """H-1 counterexample: 未 lock 时可以修改 claim.

        Counterexample: 如果未 lock 时修改 claim 也报错, 则假设不可用.
        """
        h = _make_hypothesis()
        original = h.claim
        h.claim = "未锁定的新 claim"
        assert h.claim == "未锁定的新 claim"
        assert h.claim != original

    def test_counterexample_unlocked_can_modify_prediction(self) -> None:
        """H-1 counterexample: 未 lock 时可以修改 prediction."""
        h = _make_hypothesis()
        h.prediction = "未锁定的新 prediction"
        assert h.prediction == "未锁定的新 prediction"

    def test_lock_is_idempotent(self) -> None:
        """H-1 edge: lock() 多次调用不报错."""
        h = _make_hypothesis()
        h.lock()
        h.lock()  # 第二次 lock 不报错
        assert h.immutable_after_lock is True


# ──────────────────────────────────────────────────────────────────────────
# H-2: revise() 产生新实例, 不原地修改
# ──────────────────────────────────────────────────────────────────────────


class TestH2ReviseImmutability:
    """H-2: revise() 必须返回新实例, 不原地修改原实例."""

    def test_revise_creates_new_version(self) -> None:
        """H-2 positive: revise() 返回新 Hypothesis, version+1, revised_from=原id.

        Counterexample: 如果 revise() 返回相同 id 或 version 不变, 则修订链断裂.
        """
        h = _make_hypothesis()
        revised = h.revise(
            new_claim="修订后的 claim",
            new_prediction="修订后的 prediction",
        )

        # 新实例属性校验
        assert revised.id != h.id, "revise() 必须产生新 UUID (H-2)"
        assert revised.version == h.version + 1, "revise() 必须 version+1 (H-2)"
        assert revised.revised_from == h.id, "revise() 必须 revised_from=原id (H-2)"
        assert revised.status == HypothesisStatus.PROPOSED, "修订版从 PROPOSED 开始"
        assert revised.claim == "修订后的 claim"
        assert revised.prediction == "修订后的 prediction"

    def test_counterexample_revise_does_not_mutate_original(self) -> None:
        """H-2 counterexample: revise() 不修改原实例.

        Counterexample: 如果 revise() 修改了原实例的 claim/prediction, 则不可溯源.
        """
        h = _make_hypothesis(
            claim="原始 claim",
            prediction="原始 prediction",
        )
        original_id = h.id
        original_version = h.version

        h.revise(new_claim="新 claim", new_prediction="新 prediction")

        # 原实例不变
        assert h.id == original_id, "原实例 id 不应改变"
        assert h.version == original_version, "原实例 version 不应改变"
        assert h.claim == "原始 claim", "原实例 claim 不应改变"
        assert h.prediction == "原始 prediction", "原实例 prediction 不应改变"
        assert h.status == HypothesisStatus.PROPOSED, "原实例 status 不应改变"

    def test_revise_preserves_confidence_and_creator(self) -> None:
        """H-2 positive: revise() 继承 confidence 和 created_by."""
        h = _make_hypothesis(confidence=0.8)
        revised = h.revise("新 claim", "新 prediction")
        assert revised.confidence == 0.8
        assert revised.created_by == "test_agent"


# ──────────────────────────────────────────────────────────────────────────
# H-3: falsify() 要求 evidence_against 非空
# ──────────────────────────────────────────────────────────────────────────


class TestH3FalsifyInvariants:
    """H-3: falsify() 要求 evidence_against 非空, falsified_by 在其中."""

    def test_falsify_requires_evidence_against(self) -> None:
        """H-3 positive: falsify() 在有 evidence_against 时成功.

        Counterexample: 如果 falsify() 在 evidence_against 为空时成功, 则证伪无依据.
        """
        ev_id = uuid4()
        h = _make_hypothesis(evidence_against=[ev_id])

        h.falsify(evidence_id=ev_id)

        assert h.status == HypothesisStatus.FALSIFIED
        assert h.falsified_by == ev_id

    def test_counterexample_falsify_without_evidence_raises(self) -> None:
        """H-3 counterexample: evidence_against 为空时 falsify() → raise ValueError.

        Counterexample: 如果空列表时 falsify 不报错, 则 H-3 被破坏.
        """
        h = _make_hypothesis(evidence_against=[])
        ev_id = uuid4()
        with pytest.raises(ValueError, match="evidence_against is empty"):
            h.falsify(evidence_id=ev_id)

    def test_counterexample_falsify_with_wrong_evidence_raises(self) -> None:
        """H-3 counterexample: falsified_by 不在 evidence_against 中 → raise ValueError.

        Counterexample: 如果 falsified_by 可以是任意 ID, 则证伪不可溯源.
        """
        ev_id = uuid4()
        wrong_id = uuid4()
        h = _make_hypothesis(evidence_against=[ev_id])
        with pytest.raises(ValueError, match="not in evidence_against"):
            h.falsify(evidence_id=wrong_id)

    def test_falsify_multiple_evidence_against(self) -> None:
        """H-3 positive: falsify() 允许 evidence_against 有多条时选择其中一条."""
        ev1 = uuid4()
        ev2 = uuid4()
        h = _make_hypothesis(evidence_against=[ev1, ev2])

        h.falsify(evidence_id=ev2)

        assert h.status == HypothesisStatus.FALSIFIED
        assert h.falsified_by == ev2


# ──────────────────────────────────────────────────────────────────────────
# H-4: confidence 必须 [0,1]
# ──────────────────────────────────────────────────────────────────────────


class TestH4ConfidenceBounds:
    """H-4: confidence 必须 ∈ [0.0, 1.0]."""

    @pytest.mark.parametrize("valid_confidence", [0.0, 0.5, 1.0])
    def test_confidence_bounds(self, valid_confidence: float) -> None:
        """H-4 positive: confidence 在 [0,1] 内可构造.

        Counterexample: 如果 confidence 边界值报错, 则 H-4 过严.
        """
        h = _make_hypothesis(confidence=valid_confidence)
        assert h.confidence == valid_confidence

    @pytest.mark.parametrize("invalid_confidence", [-0.1, 1.5, 100.0, -1.0])
    def test_counterexample_confidence_out_of_range(self, invalid_confidence: float) -> None:
        """H-4 counterexample: confidence 越界 → must raise ValidationError.

        Counterexample: 如果越界不报错, 则 H-4 被破坏.
        """
        with pytest.raises(ValidationError, match="confidence must be in"):
            _make_hypothesis(confidence=invalid_confidence)

    def test_confidence_default_is_0_5(self) -> None:
        """H-4 edge: confidence 默认值为 0.5."""
        h = Hypothesis(
            id=uuid4(),
            claim="test",
            prediction="test",
            created_by="test_agent",
            created_at=int(time.time()),
        )
        assert h.confidence == 0.5


# ──────────────────────────────────────────────────────────────────────────
# add_evidence: 证据管理
# ──────────────────────────────────────────────────────────────────────────


class TestAddEvidence:
    """add_evidence() 正确追加证据到对应列表 (I-10 同等珍视负结果)."""

    def test_add_evidence_appends_correctly(self) -> None:
        """add_evidence(supports=True) → evidence_for, False → evidence_against.

        I-10: NegativeResult 与 Positive Evidence 同等存储、同等索引.
        """
        h = _make_hypothesis()
        ev_for = uuid4()
        ev_against = uuid4()

        h.add_evidence(ev_for, supports=True)
        h.add_evidence(ev_against, supports=False)

        assert ev_for in h.evidence_for
        assert ev_against in h.evidence_against
        assert ev_for not in h.evidence_against
        assert ev_against not in h.evidence_for

    def test_add_evidence_deduplicates(self) -> None:
        """add_evidence() 重复添加同一证据不产生重复条目."""
        h = _make_hypothesis()
        ev_id = uuid4()

        h.add_evidence(ev_id, supports=True)
        h.add_evidence(ev_id, supports=True)  # 重复

        assert h.evidence_for.count(ev_id) == 1


# ──────────────────────────────────────────────────────────────────────────
# 构造期不变量: 必填字段约束
# ──────────────────────────────────────────────────────────────────────────


class TestConstructionInvariants:
    """Hypothesis 构造期不变量."""

    def test_required_fields(self) -> None:
        """构造时必须提供 id/claim/prediction/created_by/created_at.

        Counterexample: 缺少必填字段 → raise ValidationError.
        """
        with pytest.raises(ValidationError):
            Hypothesis(claim="test", prediction="test")  # type: ignore[call-arg]

    def test_initial_status_is_proposed(self) -> None:
        """默认 status 为 PROPOSED."""
        h = _make_hypothesis()
        assert h.status == HypothesisStatus.PROPOSED

    def test_initial_version_is_one(self) -> None:
        """默认 version 为 1."""
        h = _make_hypothesis()
        assert h.version == 1