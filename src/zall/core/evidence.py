"""zall.core.evidence — Evidence + NegativeResult (E3 Science Kit, docs/E3_SCIENCE_KIT.md §2.3).

Corresponds to:
  docs/E3_SCIENCE_KIT.md §2.3  Evidence + NegativeResult — 证据管理与负结果一等公民
  MASTER.md §1.5               HypothesisGoal 作为 Commitment 扩展
  MASTER.md §3.3               评估体系 — 实验完成判定
  MASTER.md §10 I-10           负结果平等 — 负结果与正结果同等存储、同等索引、同等珍视

IPR constraints:
  IPR-3: only pydantic / stdlib, no model SDK
"""

from __future__ import annotations

from enum import Enum
from uuid import UUID

from pydantic import BaseModel

from zall.core.provenance import ScienceProvenance


class EvidenceType(str, Enum):
    """证据类型 (E3_SCIENCE_KIT.md §2.3).

    NEGATIVE 与 POSITIVE 同等珍视 (I-10):
        "假设被证伪"不是"失败", 是"有价值的信息"。
    """
    POSITIVE = "positive"       # 支持假设
    NEGATIVE = "negative"       # 反驳假设 (负结果, 同等珍视)
    INCONCLUSIVE = "inconclusive"  # 无法判定


class Evidence(BaseModel):
    """单条实验证据 (E3_SCIENCE_KIT.md §2.3).

    通过 hypothesis_id 和 experiment_id 引用假设和实验 (UUID, 不直接 import)。
    """

    id: UUID
    hypothesis_id: UUID
    experiment_id: UUID
    type: EvidenceType
    metric: str                     # "G-F Score", "p-value", "rho"
    value: float
    threshold: float | None = None  # 判据阈值
    supports: bool                  # True=支持, False=反驳
    provenance: ScienceProvenance   # 完整血缘
    created_at: int


class NegativeResult(BaseModel):
    """负结果一等公民 (E3_SCIENCE_KIT.md §2.3, I-10).

    负结果与正结果同等存储、同等索引、同等珍视。
    "假设被证伪"不是"失败", 是"有价值的信息"。

    此类型包装一个 type=NEGATIVE 的 Evidence, 附加诊断信息,
    说明"排除了什么可能性" (科学价值)。

    索引策略: NegativeResult 不独立存储, 通过其 evidence 字段
    与正结果共存于同一 evidence 集合中, 实现同等索引 (I-10)。
    """

    evidence: Evidence  # type=NEGATIVE 的 evidence
    what_failed: str    # 具体什么失败了
    conditions: dict    # 实验条件
    diagnosis: str      # 诊断: 假设错? 实验设计错? 执行误差?
    value: str          # 科学价值: 排除了什么可能性