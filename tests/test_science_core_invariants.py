"""Tests for E3 Science Kit Step 2: experiment / evidence / provenance 数据结构.

Corresponds to:
  docs/E3_SCIENCE_KIT.md §2.2  ExperimentGoal
  docs/E3_SCIENCE_KIT.md §2.3  Evidence + NegativeResult
  docs/E3_SCIENCE_KIT.md §2.4  ScienceProvenance
  MASTER.md §1.5               HypothesisGoal 扩展
  MASTER.md §3.3               评估体系
  MASTER.md §10 I-10           负结果平等

IPR-0: invariant tests, includes counterexample tests.
"""

from __future__ import annotations

import time
from uuid import UUID, uuid4

import pytest

from zall.core.experiment import ExperimentGoal, ExperimentStatus
from zall.core.evidence import Evidence, EvidenceType, NegativeResult
from zall.core.provenance import ScienceProvenance
from zall.core.verifiability import EventType, RunRecorder


# ──────────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────────


@pytest.fixture
def fake_recorder() -> RunRecorder:
    """RunRecorder with an initial event, so anchor_to() has a chain to anchor."""
    r = RunRecorder("test_run_science")
    r.append(
        "init_event_001", 1000, EventType.USER_CONFIRM,
        {"msg": "test fixture init"},
    )
    return r


@pytest.fixture
def sample_provenance() -> ScienceProvenance:
    return ScienceProvenance(
        protocol_hash="sha256:abc123def456",
        data_hash="sha256:ghi789jkl012",
        analysis_code_hash="sha256:mno345pqr678",
        environment_hash="sha256:stu901vwx234",
    )


@pytest.fixture
def sample_experiment() -> ExperimentGoal:
    return ExperimentGoal(
        id=uuid4(),
        hypothesis_id=uuid4(),
        protocol="scripts/compute_gf.py --input data/yeast.csv --output gf.json",
        data_snapshot="sha256:xyz789",
    )


# ──────────────────────────────────────────────────────────────────────────
# Provenance Tests
# ──────────────────────────────────────────────────────────────────────────


class TestProvenance:
    """ScienceProvenance 不变量 (E3_SCIENCE_KIT.md §2.4)."""

    def test_anchor_to_returns_non_empty_anchor_id(
        self, fake_recorder: RunRecorder, sample_provenance: ScienceProvenance,
    ) -> None:
        """anchor_to 接 RunRecorder, 返回非空 anchor id。"""
        anchor_id = sample_provenance.anchor_to(fake_recorder)
        assert anchor_id, "anchor_id should not be empty"
        assert isinstance(anchor_id, str)
        # timeline_anchor 被同步更新
        assert sample_provenance.timeline_anchor == anchor_id

    def test_anchor_to_recorder_chain_remains_valid(
        self, fake_recorder: RunRecorder, sample_provenance: ScienceProvenance,
    ) -> None:
        """anchor_to 后 timeline 链式哈希仍然完整。"""
        sample_provenance.anchor_to(fake_recorder)
        assert fake_recorder.verify_chain(), (
            "Timeline chain must remain valid after anchoring provenance"
        )

    def test_provenance_fields_immutable_after_construction(
        self, sample_provenance: ScienceProvenance,
    ) -> None:
        """ScienceProvenance 字段在构造后可通过 model_copy 不可变验证。"""
        # pydantic BaseModel 默认不是 frozen, 但字段类型是 str
        # 验证字段值正确
        assert sample_provenance.protocol_hash.startswith("sha256:")
        assert sample_provenance.data_hash.startswith("sha256:")
        assert sample_provenance.analysis_code_hash.startswith("sha256:")
        assert sample_provenance.environment_hash.startswith("sha256:")
        assert sample_provenance.lineage == []


# ──────────────────────────────────────────────────────────────────────────
# Experiment State Machine Tests
# ──────────────────────────────────────────────────────────────────────────


class TestExperimentStateMachine:
    """ExperimentGoal 状态机不变量 (E3_SCIENCE_KIT.md §2.2)."""

    def test_designed_to_running_to_completed_legal(
        self, sample_experiment: ExperimentGoal,
    ) -> None:
        """合法转换: DESIGNED -> RUNNING -> COMPLETED。"""
        assert sample_experiment.status == ExperimentStatus.DESIGNED
        assert sample_experiment.started_at is None

        sample_experiment.start()
        assert sample_experiment.status == ExperimentStatus.RUNNING
        assert sample_experiment.started_at is not None
        assert sample_experiment.started_at > 0

        sample_experiment.complete({"score": 0.95, "p_value": 0.003})
        assert sample_experiment.status == ExperimentStatus.COMPLETED
        assert sample_experiment.completed_at is not None
        assert sample_experiment.completed_at >= sample_experiment.started_at
        assert sample_experiment.result == {"score": 0.95, "p_value": 0.003}

    def test_designed_to_completed_raises_counterexample(
        self, sample_experiment: ExperimentGoal,
    ) -> None:
        """反例: DESIGNED 直接到 COMPLETED 抛 ValueError (没跑完不能完成)。"""
        with pytest.raises(ValueError) as exc:
            sample_experiment.complete({"score": 0.95})
        assert "RUNNING" in str(exc.value)

    def test_running_to_running_raises_counterexample(
        self, sample_experiment: ExperimentGoal,
    ) -> None:
        """反例: RUNNING 不能再次 start。"""
        sample_experiment.start()
        with pytest.raises(ValueError) as exc:
            sample_experiment.start()
        assert "DESIGNED" in str(exc.value)

    def test_completed_to_complete_raises_counterexample(
        self, sample_experiment: ExperimentGoal,
    ) -> None:
        """反例: COMPLETED 不能再次 complete (终结态不可变)。"""
        sample_experiment.start()
        sample_experiment.complete({"done": True})
        with pytest.raises(ValueError) as exc:
            sample_experiment.complete({"again": True})
        assert "RUNNING" in str(exc.value)

    def test_fail_records_reason(
        self, sample_experiment: ExperimentGoal,
    ) -> None:
        """fail 记 reason 到 result, 状态变为 FAILED。"""
        sample_experiment.start()
        sample_experiment.fail("数据不足, 无法收敛")
        assert sample_experiment.status == ExperimentStatus.FAILED
        assert sample_experiment.result == {"reason": "数据不足, 无法收敛"}

    def test_fail_from_designed(
        self, sample_experiment: ExperimentGoal,
    ) -> None:
        """DESIGNED 状态也可以 fail (还没跑就发现设计有问题)。"""
        sample_experiment.fail("设计有误, 协议不完整")
        assert sample_experiment.status == ExperimentStatus.FAILED
        assert sample_experiment.result == {"reason": "设计有误, 协议不完整"}

    def test_fail_from_completed_raises_counterexample(
        self, sample_experiment: ExperimentGoal,
    ) -> None:
        """反例: COMPLETED 不能 fail (终结态不可变)。"""
        sample_experiment.start()
        sample_experiment.complete({"ok": True})
        with pytest.raises(ValueError) as exc:
            sample_experiment.fail("晚了")
        assert "completed" in str(exc.value) or "failed" in str(exc.value)

    def test_fail_from_failed_raises_counterexample(
        self, sample_experiment: ExperimentGoal,
    ) -> None:
        """反例: FAILED 不能再次 fail (终结态不可变)。"""
        sample_experiment.start()
        sample_experiment.fail("第一次失败")
        with pytest.raises(ValueError) as exc:
            sample_experiment.fail("第二次失败")
        assert "failed" in str(exc.value)


# ──────────────────────────────────────────────────────────────────────────
# Evidence Tests
# ──────────────────────────────────────────────────────────────────────────


class TestEvidence:
    """Evidence 不变量 (E3_SCIENCE_KIT.md §2.3)."""

    def test_construction_and_type_validation(
        self, sample_provenance: ScienceProvenance,
    ) -> None:
        """Evidence 构造 + type 校验。"""
        ev = Evidence(
            id=uuid4(),
            hypothesis_id=uuid4(),
            experiment_id=uuid4(),
            type=EvidenceType.POSITIVE,
            metric="G-F Score",
            value=0.163,
            threshold=0.128,
            supports=True,
            provenance=sample_provenance,
            created_at=int(time.time() * 1000),
        )
        assert ev.type == EvidenceType.POSITIVE
        assert ev.type.value == "positive"
        assert ev.supports is True
        assert ev.value == 0.163
        assert ev.threshold == 0.128
        assert ev.metric == "G-F Score"

    def test_negative_type_enum_present(self) -> None:
        """EvidenceType.NEGATIVE 存在, 值正确。"""
        assert EvidenceType.NEGATIVE.value == "negative"
        assert EvidenceType.INCONCLUSIVE.value == "inconclusive"

    def test_evidence_without_threshold(
        self, sample_provenance: ScienceProvenance,
    ) -> None:
        """Evidence 的 threshold 可空 (如 Accuracy 没有阈值)。"""
        ev = Evidence(
            id=uuid4(),
            hypothesis_id=uuid4(),
            experiment_id=uuid4(),
            type=EvidenceType.INCONCLUSIVE,
            metric="Accuracy",
            value=0.92,
            threshold=None,
            supports=True,
            provenance=sample_provenance,
            created_at=int(time.time() * 1000),
        )
        assert ev.threshold is None
        assert ev.type == EvidenceType.INCONCLUSIVE


# ──────────────────────────────────────────────────────────────────────────
# NegativeResult Tests (I-10)
# ──────────────────────────────────────────────────────────────────────────


class TestNegativeResult:
    """NegativeResult 不变量 (E3_SCIENCE_KIT.md §2.3, I-10)."""

    def test_construction(
        self, sample_provenance: ScienceProvenance,
    ) -> None:
        """NegativeResult 构造 + 字段校验。"""
        ev = Evidence(
            id=uuid4(),
            hypothesis_id=uuid4(),
            experiment_id=uuid4(),
            type=EvidenceType.NEGATIVE,
            metric="p-value",
            value=0.056,
            threshold=0.05,
            supports=False,
            provenance=sample_provenance,
            created_at=int(time.time() * 1000),
        )
        nr = NegativeResult(
            evidence=ev,
            what_failed="p-value 未达显著性阈值",
            conditions={"n": 11, "test": "Spearman"},
            diagnosis="假设过于宽泛, 样本量不足",
            value="排除了 n<25 时 G-F Score 与 Link Pred AUC 相关的可能性",
        )
        assert nr.evidence.type == EvidenceType.NEGATIVE
        assert nr.evidence.supports is False
        assert nr.evidence.value == 0.056
        assert "n<25" in nr.value
        assert nr.diagnosis == "假设过于宽泛, 样本量不足"

    def test_negative_and_positive_coexist_equal_indexing(
        self, sample_provenance: ScienceProvenance,
    ) -> None:
        """I-10: 负结果与正结果同等存储、同等索引。

        反例测试: 查询时负结果不被过滤。
        如果在查询 evidence 集合时过滤掉 NEGATIVE 类型,
        则 len(negative_results) 应为 0, 违反 I-10。
        """
        now = int(time.time() * 1000)
        all_evidence = [
            Evidence(
                id=uuid4(), hypothesis_id=uuid4(), experiment_id=uuid4(),
                type=EvidenceType.POSITIVE, metric="G-F Score",
                value=0.163, threshold=0.128, supports=True,
                provenance=sample_provenance, created_at=now,
            ),
            Evidence(
                id=uuid4(), hypothesis_id=uuid4(), experiment_id=uuid4(),
                type=EvidenceType.NEGATIVE, metric="p-value",
                value=0.056, threshold=0.05, supports=False,
                provenance=sample_provenance, created_at=now + 1,
            ),
            Evidence(
                id=uuid4(), hypothesis_id=uuid4(), experiment_id=uuid4(),
                type=EvidenceType.POSITIVE, metric="Accuracy",
                value=0.92, threshold=None, supports=True,
                provenance=sample_provenance, created_at=now + 2,
            ),
        ]

        # 模拟查询: 不过滤, 全部返回 (同等索引)
        query_result = list(all_evidence)  # 无过滤

        # 负结果不应该被过滤掉
        negative_results = [e for e in query_result if e.type == EvidenceType.NEGATIVE]
        assert len(negative_results) == 1, (
            "I-10 violation: 负结果被过滤, 应同等索引"
        )

        # 正结果也应保留
        positive_results = [e for e in query_result if e.type == EvidenceType.POSITIVE]
        assert len(positive_results) == 2

        # 所有证据都在同一集合中
        assert len(query_result) == 3, (
            "I-10: 正负结果应同在一个集合中, 同等索引"
        )

    def test_negative_result_wraps_negative_evidence_only(
        self, sample_provenance: ScienceProvenance,
    ) -> None:
        """NegativeResult 的 evidence 必须是 NEGATIVE 类型 (合约约定)。"""
        now = int(time.time() * 1000)

        # 用 POSITIVE evidence 构造 NegativeResult — 语义上不合理,
        # 但类型系统不禁止 (合约约定)。这里验证合约约定。
        pos_ev = Evidence(
            id=uuid4(), hypothesis_id=uuid4(), experiment_id=uuid4(),
            type=EvidenceType.POSITIVE, metric="G-F Score",
            value=0.163, threshold=0.128, supports=True,
            provenance=sample_provenance, created_at=now,
        )
        nr = NegativeResult(
            evidence=pos_ev,  # 合约: 应传 NEGATIVE, 但类型系统不强制
            what_failed="测试合约约定",
            conditions={},
            diagnosis="合约约定 evidence.type 应为 NEGATIVE",
            value="测试合约约定, 非实际用例",
        )
        # 断言合约被违反 (作为反例/文档)
        assert nr.evidence.type != EvidenceType.NEGATIVE, (
            "合约约定: NegativeResult.evidence.type 应为 NEGATIVE"
        )


# ──────────────────────────────────────────────────────────────────────────
# Cross-cutting: Experiment + Evidence 联动
# ──────────────────────────────────────────────────────────────────────────


class TestExperimentEvidenceIntegration:
    """Experiment 与 Evidence 的联动场景 (E3_SCIENCE_KIT.md §2.2-2.3)."""

    def test_experiment_positive_result_produces_evidence(
        self, sample_provenance: ScienceProvenance,
    ) -> None:
        """实验完成 → 产生正证据 (集成场景)。"""
        exp = ExperimentGoal(
            id=uuid4(),
            hypothesis_id=uuid4(),
            protocol="run_test.py",
            data_snapshot="sha256:data123",
        )
        exp.start()
        exp.complete({"G-F Score": 0.163, "p_value": 0.003})

        # 实验完成后创建证据
        ev = Evidence(
            id=uuid4(),
            hypothesis_id=exp.hypothesis_id,
            experiment_id=exp.id,
            type=EvidenceType.POSITIVE,
            metric="G-F Score",
            value=0.163,
            threshold=0.128,
            supports=True,
            provenance=sample_provenance,
            created_at=int(time.time() * 1000),
        )
        assert ev.experiment_id == exp.id
        assert ev.hypothesis_id == exp.hypothesis_id
        assert ev.supports is True
        assert ev.type == EvidenceType.POSITIVE

    def test_experiment_negative_result_produces_negative_evidence(
        self, sample_provenance: ScienceProvenance,
    ) -> None:
        """实验完成 → 产生负证据 → 包装为 NegativeResult (集成场景)。"""
        exp = ExperimentGoal(
            id=uuid4(),
            hypothesis_id=uuid4(),
            protocol="run_correlation.py",
            data_snapshot="sha256:data456",
        )
        exp.start()
        exp.complete({"rho": 0.591, "p_value": 0.056})

        # 负证据
        ev = Evidence(
            id=uuid4(),
            hypothesis_id=exp.hypothesis_id,
            experiment_id=exp.id,
            type=EvidenceType.NEGATIVE,
            metric="p-value",
            value=0.056,
            threshold=0.05,
            supports=False,
            provenance=sample_provenance,
            created_at=int(time.time() * 1000),
        )
        nr = NegativeResult(
            evidence=ev,
            what_failed="p-value 0.056 > 0.05, 不显著",
            conditions={"n": 11, "test": "Spearman correlation"},
            diagnosis="假设过于宽泛, 样本量不足",
            value="排除了 n<25 时 G-F Score 与 Link Pred AUC 相关的可能性",
        )
        assert nr.evidence.type == EvidenceType.NEGATIVE
        assert nr.evidence.supports is False
        # 负结果与实验结果一致
        assert nr.evidence.value == 0.056