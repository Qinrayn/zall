"""zall.core.experiment — ExperimentGoal (E3 Science Kit, docs/E3_SCIENCE_KIT.md §2.2).

Corresponds to:
  docs/E3_SCIENCE_KIT.md §2.2  ExperimentGoal — 实验设计、执行和结果
  MASTER.md §1.5               HypothesisGoal 作为 Commitment 扩展
  MASTER.md §3.3               评估体系 — 实验完成判定
  MASTER.md §10 I-10           负结果平等 — 负结果与正结果同等珍视

IPR constraints:
  IPR-3: only pydantic / stdlib, no model SDK
"""

from __future__ import annotations

import time
from enum import Enum
from uuid import UUID

from pydantic import BaseModel


class ExperimentStatus(str, Enum):
    """实验状态机 (E3_SCIENCE_KIT.md §2.2).

    合法转换:
        DESIGNED -> RUNNING -> COMPLETED   (正常执行)
        DESIGNED | RUNNING -> FAILED       (失败终止)

    非法转换 (抛 ValueError):
        DESIGNED -> COMPLETED              (没跑完不能完成)
        RUNNING -> RUNNING                 (不能重复 start)
        COMPLETED -> COMPLETE / FAILED     (终结态不可变)
        FAILED -> 任何状态                  (终结态不可变)
    """
    DESIGNED = "designed"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class ExperimentGoal(BaseModel):
    """实验目标 (E3_SCIENCE_KIT.md §2.2).

    记录实验设计、执行和结果, 与 Hypothesis 通过 UUID 引用 (不直接 import)。

    状态机守卫:
        start()    只允许 DESIGNED -> RUNNING
        complete() 只允许 RUNNING -> COMPLETED
        fail()     允许 DESIGNED | RUNNING -> FAILED
    """

    id: UUID
    hypothesis_id: UUID  # 引用 Hypothesis, 不 import, 仅用 UUID
    protocol: str        # 实验步骤 (脚本路径 + 参数)
    data_snapshot: str   # 输入数据 hash (接 ScienceProvenance)
    result: dict | None = None
    status: ExperimentStatus = ExperimentStatus.DESIGNED
    reproducible: bool = True
    started_at: int | None = None
    completed_at: int | None = None

    def start(self) -> None:
        """DESIGNED -> RUNNING, 记 started_at 为当前时间戳 (毫秒)。"""
        if self.status != ExperimentStatus.DESIGNED:
            raise ValueError(
                f"Cannot start experiment in status {self.status.value}; "
                f"expected DESIGNED"
            )
        self.status = ExperimentStatus.RUNNING
        self.started_at = int(time.time() * 1000)

    def complete(self, result: dict) -> None:
        """RUNNING -> COMPLETED, 记 result 和 completed_at。

        Args:
            result: 实验输出 key metrics (如 {"G-F Score": 0.163})
        """
        if self.status != ExperimentStatus.RUNNING:
            raise ValueError(
                f"Cannot complete experiment in status {self.status.value}; "
                f"expected RUNNING"
            )
        self.result = result
        self.status = ExperimentStatus.COMPLETED
        self.completed_at = int(time.time() * 1000)

    def fail(self, reason: str) -> None:
        """DESIGNED | RUNNING -> FAILED, result 存 reason。

        Args:
            reason: 失败原因 (如 "数据不足, 无法收敛")
        """
        if self.status in (ExperimentStatus.COMPLETED, ExperimentStatus.FAILED):
            raise ValueError(
                f"Cannot fail experiment in status {self.status.value}; "
                f"expected DESIGNED or RUNNING"
            )
        self.result = {"reason": reason}
        self.status = ExperimentStatus.FAILED