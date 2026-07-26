"""zall.core.hypothesis — Hypothesis lifecycle + evidence management (E3_SCIENCE_KIT.md §2.1).

Corresponds to:
  §2.1   HypothesisStatus / Hypothesis  —— 假设生命周期 + 证据管理
  H-1    lock() 后 claim/prediction 不可变
  H-2    revise() 产生新 UUID + version+1 + revised_from 指向原
  H-3    falsify() 要求 evidence_against 非空且 falsified_by 在其中
  H-4    confidence 必须 [0,1]

IPR constraints:
  IPR-0: invariant tests at tests/test_hypothesis_invariants.py, includesCounterexample
  IPR-1: this file corresponds to E3_SCIENCE_KIT.md §2.1
  IPR-3: pydantic / stdlib only, no model SDK
  IPR-4: this file is a primitive, no main Loop
"""

from __future__ import annotations

import time
from enum import Enum
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, PrivateAttr, field_validator, model_validator


# ──────────────────────────────────────────────────────────────────────────
# §2.1 HypothesisStatus Enum
# ──────────────────────────────────────────────────────────────────────────


class HypothesisStatus(str, Enum):
    """假设生命周期状态 (E3_SCIENCE_KIT.md §2.1)。

    PROPOSED  →  TESTING  →  CONFIRMED | FALSIFIED
                  ↑                        |
                  └────── REVISED ←────────┘
    """

    PROPOSED = "proposed"
    TESTING = "testing"
    CONFIRMED = "confirmed"
    FALSIFIED = "falsified"
    REVISED = "revised"  # 证伪后修订为新版本


# ──────────────────────────────────────────────────────────────────────────
# §2.1 Hypothesis (核心数据结构)
# ──────────────────────────────────────────────────────────────────────────


class Hypothesis(BaseModel):
    """假设 — 核心数据结构 (E3_SCIENCE_KIT.md §2.1)。

    支持完整生命周期: 提出 → 实验 → 收集证据 → 确认/证伪 → 修订.

    IPR-0 不变量:
        H-1: lock() 后 claim/prediction 不可变 (手动检查 + 反例测试)
        H-2: revise() 返回新实例, 不原地修改
        H-3: falsify() 要求 evidence_against 非空, falsified_by 在其中
        H-4: confidence ∈ [0, 1] (pydantic field_validator)
    """

    model_config = ConfigDict(validate_assignment=True)

    id: UUID
    claim: str
    prediction: str
    confidence: float = 0.5
    evidence_for: list[UUID] = []
    evidence_against: list[UUID] = []
    status: HypothesisStatus = HypothesisStatus.PROPOSED
    version: int = 1
    revised_from: UUID | None = None
    falsified_by: UUID | None = None
    created_by: str
    created_at: int
    immutable_after_lock: bool = False

    # Private: 存储 lock() 时的快照, 用于 H-1 检查
    _locked_claim: str | None = PrivateAttr(default=None)
    _locked_prediction: str | None = PrivateAttr(default=None)

    # ── H-4: confidence 范围校验 ──

    @field_validator("confidence")
    @classmethod
    def _confidence_range(cls, v: float) -> float:
        """H-4: confidence must be in [0.0, 1.0] (E3_SCIENCE_KIT.md §2.1).

        Counterexample: confidence=1.5 → 须 raise ValidationError.
        """
        if not (0.0 <= v <= 1.0):
            raise ValueError(
                f"confidence must be in [0.0, 1.0], got {v} "
                f"(H-4: E3_SCIENCE_KIT.md §2.1)"
            )
        return v

    # ── H-1: lock() 后 claim/prediction 不可变 ──

    def lock(self) -> None:
        """H-1: 锁定假设, 之后 claim/prediction 不可修改 (E3_SCIENCE_KIT.md §2.1).

        锁定后任何修改 claim/prediction 的尝试都会 raise ValueError.
        """
        # 先存快照, 再设锁标志 (顺序重要: 设标志会触发 model_validator)
        self._locked_claim = self.claim
        self._locked_prediction = self.prediction
        self.immutable_after_lock = True

    @model_validator(mode="after")
    def _immutable_after_lock(self) -> Hypothesis:
        """H-1: 验证 lock 后 claim/prediction 未被修改.

        Counterexample: lock() 后修改 claim → 须 raise ValueError.
        """
        if self.immutable_after_lock and self._locked_claim is not None:
            if self.claim != self._locked_claim:
                raise ValueError(
                    "Hypothesis is locked: claim cannot be modified (H-1: "
                    "E3_SCIENCE_KIT.md §2.1)"
                )
            if self.prediction != self._locked_prediction:
                raise ValueError(
                    "Hypothesis is locked: prediction cannot be modified (H-1: "
                    "E3_SCIENCE_KIT.md §2.1)"
                )
        return self

    # ── 证据管理 ──

    def add_evidence(self, ev_id: UUID, supports: bool) -> None:
        """添加证据到 evidence_for 或 evidence_against.

        Args:
            ev_id: 证据 ID (Evidence 实体的 UUID).
            supports: True → evidence_for, False → evidence_against.

        I-10: NegativeResult 与 Positive Evidence 同等存储、同等索引.
        """
        if supports:
            if ev_id not in self.evidence_for:
                self.evidence_for.append(ev_id)
        else:
            if ev_id not in self.evidence_against:
                self.evidence_against.append(ev_id)

    # ── H-2: revise() ──

    def revise(self, new_claim: str, new_prediction: str) -> Hypothesis:
        """H-2: 修订假设, 返回新版本实例 (不原地修改).

        返回新 Hypothesis:
            - 新 UUID
            - version = self.version + 1
            - revised_from = self.id
            - status = PROPOSED (新假设从零开始)
            - 其他字段继承自原假设 (confidence, created_by)

        Counterexample: 此方法不修改自身; 调用后原实例保持不变.
        """
        return Hypothesis(
            id=uuid4(),
            claim=new_claim,
            prediction=new_prediction,
            confidence=self.confidence,
            evidence_for=[],
            evidence_against=[],
            status=HypothesisStatus.PROPOSED,
            version=self.version + 1,
            revised_from=self.id,
            falsified_by=None,
            created_by=self.created_by,
            created_at=int(time.time()),
            immutable_after_lock=False,
        )

    # ── H-3: falsify() ──

    def falsify(self, evidence_id: UUID) -> None:
        """H-3: 证伪假设 (E3_SCIENCE_KIT.md §2.1).

        要求:
            - evidence_against 非空 (否则 raise ValueError)
            - evidence_id 必须在 evidence_against 中 (否则 raise ValueError)

        效果:
            - falsified_by = evidence_id
            - status = FALSIFIED

        Counterexample: evidence_against 为空时调用 → 须 raise ValueError.
        """
        if not self.evidence_against:
            raise ValueError(
                "Cannot falsify: evidence_against is empty (H-3: "
                "E3_SCIENCE_KIT.md §2.1)"
            )
        if evidence_id not in self.evidence_against:
            raise ValueError(
                f"falsified_by {evidence_id} not in evidence_against (H-3: "
                f"E3_SCIENCE_KIT.md §2.1)"
            )
        self.falsified_by = evidence_id
        self.status = HypothesisStatus.FALSIFIED