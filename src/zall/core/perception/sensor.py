"""zall.core.perception.sensor — Sensor protocol + Observation / Percept / StateEstimate.

Corresponds to:
  §4.2.3  Perception Engine: Sensor → Observation → Percept → StateEstimate
  FR-2    感知不确定性: Observation / Percept / StateEstimate 必须携带置信度
  FR-0    统一性: Sensor 抽象同时适用于具身 (摄像头/力矩) 和非具身 (文件/git)

设计:
  Sensor (Protocol)  — 传感器接口, 每种传感器实现 observe() 返回 Observation
  Observation        — 原始观测数据 (带置信度、元数据)
  Percept            — 结构化感知 (解释后的数据)
  StateEstimate      — 多传感器融合后的状态估计 (带协方差/不确定性)

IPR constraints:
  IPR-0: invariant tests at tests/test_perception_invariants.py
  IPR-3: pydantic / stdlib only, no model SDK
"""

from __future__ import annotations

import time
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, field_validator

# ──────────────────────────────────────────────────────────────────────────
# Observation — 原始观测数据
# ──────────────────────────────────────────────────────────────────────────


class Observation(BaseModel):
    """原始传感器观测数据 (MASTER.md §4.2.3)。

    IPR-0 不变量:
        - frozen
        - sensor_id 非空
        - confidence ∈ [0, 1] (FR-2: 感知不确定性)
        - timestamp 是合理的 unix 毫秒时间戳

    Counterexample:
        confidence=1.5 → 须 raise ValueError
        sensor_id="" → 须 raise ValueError
    """

    model_config = ConfigDict(frozen=True)

    sensor_id: str
    """传感器标识 (如 "file", "git", "codegraph", "lsp")"""

    timestamp: int
    """观测时间戳 (unix 毫秒)"""

    data: dict[str, Any]
    """原始观测数据 (键值对)"""

    confidence: float
    """观测置信度 [0, 1] — 1=确定, 0=完全不确定 (FR-2)"""

    metadata: dict[str, Any] = {}
    """传感器元数据 (采样率、精度、噪声模型等)"""

    @field_validator("confidence")
    @classmethod
    def _confidence_in_range(cls, v: float) -> float:
        """FR-2: 置信度必须在 [0, 1] 范围内。

        Counterexample: confidence=1.5 → 须 raise ValueError。
        """
        if not 0.0 <= v <= 1.0:
            raise ValueError(
                f"confidence must be in [0.0, 1.0], got {v} (FR-2)"
            )
        return v

    @field_validator("sensor_id")
    @classmethod
    def _sensor_id_nonempty(cls, v: str) -> str:
        """sensor_id 必须非空。

        Counterexample: sensor_id="" → 须 raise ValueError。
        """
        if not v or not v.strip():
            raise ValueError("sensor_id must be non-empty")
        return v.strip()

    @classmethod
    def create(
        cls,
        sensor_id: str,
        data: dict[str, Any],
        confidence: float = 1.0,
        metadata: dict[str, Any] | None = None,
    ) -> Observation:
        """便捷构造方法 (自动填充 timestamp)。"""
        return cls(
            sensor_id=sensor_id,
            timestamp=int(time.time() * 1000),
            data=data,
            confidence=confidence,
            metadata=metadata or {},
        )


# ──────────────────────────────────────────────────────────────────────────
# Percept — 结构化感知
# ──────────────────────────────────────────────────────────────────────────


class Percept(BaseModel):
    """结构化感知——解释后的观测 (MASTER.md §4.2.3)。

    Observation 是"原始数据", Percept 是"解释后的数据"。
    例如: Observation 是文件内容, Percept 是"文件包含类定义 X"。

    IPR-0 不变量:
        - frozen
        - confidence ∈ [0, 1]
        - uncertainty ≥ 0
        - source 非空

    Counterexample:
        confidence=-0.5 → 须 raise ValueError
    """

    model_config = ConfigDict(frozen=True)

    state: dict[str, Any]
    """结构化状态 (如 {"class_name": "Foo", "methods": ["bar", "baz"]})"""

    confidence: float
    """解释置信度 [0, 1]"""

    uncertainty: float = 0.0
    """不确定性度量 (熵/方差) — 越大越不确定"""

    timestamp: int
    """解释时间戳 (unix 毫秒)"""

    source: str
    """源传感器标识"""

    @field_validator("confidence")
    @classmethod
    def _confidence_in_range(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError(
                f"confidence must be in [0.0, 1.0], got {v}"
            )
        return v

    @field_validator("uncertainty")
    @classmethod
    def _uncertainty_non_negative(cls, v: float) -> float:
        if v < 0:
            raise ValueError(
                f"uncertainty must be >= 0, got {v}"
            )
        return v

    @field_validator("source")
    @classmethod
    def _source_nonempty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("source must be non-empty")
        return v.strip()


# ──────────────────────────────────────────────────────────────────────────
# StateEstimate — 多传感器融合状态估计
# ──────────────────────────────────────────────────────────────────────────


class StateEstimate(BaseModel):
    """多传感器融合后的状态估计 (MASTER.md §4.2.3)。

    这是 Perception Engine 的最终输出——结合所有传感器信息后,
    对当前世界状态的统一估计。

    IPR-0 不变量:
        - frozen
        - confidence ∈ [0, 1]
        - sources 非空 (必须有至少一个来源)
        - 每个 confidence 值 ∈ [0, 1]

    Counterexample:
        sources=[] → 须 raise ValueError (没有来源的状态估计不可信)
    """

    model_config = ConfigDict(frozen=True)

    state: dict[str, Any]
    """融合后的状态 (如 {"file": "modified", "git_sha": "abc123", "test_status": "passing"})"""

    confidence: float
    """整体置信度 [0, 1] — 多传感器融合后的综合置信度"""

    covariance: dict[str, float] = {}
    """各维度不确定性 (键=状态键, 值=不确定性度量)"""

    sources: list[str]
    """来源传感器 ID 列表 (溯源用)"""

    timestamp: int
    """状态估计时间戳 (unix 毫秒)"""

    @field_validator("confidence")
    @classmethod
    def _confidence_in_range(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError(
                f"confidence must be in [0.0, 1.0], got {v}"
            )
        return v

    @field_validator("sources")
    @classmethod
    def _sources_nonempty(cls, v: list[str]) -> list[str]:
        """StateEstimate 必须至少有一个来源。

        Counterexample: sources=[] → 须 raise ValueError。
        """
        if not v:
            raise ValueError(
                "sources must be non-empty (至少有一个传感器来源)"
            )
        return v

    @classmethod
    def empty(cls) -> StateEstimate:
        """构造一个空状态估计 (用于初始状态)。"""
        return cls(
            state={},
            confidence=0.0,
            sources=["none"],
            timestamp=int(time.time() * 1000),
        )


# ──────────────────────────────────────────────────────────────────────────
# Sensor Protocol — 传感器接口
# ──────────────────────────────────────────────────────────────────────────


@runtime_checkable
class Sensor(Protocol):
    """传感器接口 (MASTER.md §4.2.3)。

    每个传感器实现 observe() 方法, 返回一个 Observation。
    传感器可以是:
      - 文件系统传感器 (FileSensor)
      - Git 状态传感器 (GitSensor)
      - 代码图传感器 (CodeGraphSensor)
      - LSP 诊断传感器 (LSPSensor)
      - 摄像头传感器 (具身场景)
      - 力矩传感器 (具身场景)

    sensor_id 属性用于标识传感器类型, 也用于 StateEstimate.sources 溯源。
    """

    @property
    def sensor_id(self) -> str:
        """传感器唯一标识 (如 "file", "git", "codegraph")。"""
        ...

    def observe(self) -> Observation:
        """执行一次观测, 返回当前传感器读数。

        纯函数语义: 不修改传感器内部状态, 每次调用返回当前快照。
        实际实现可能涉及 I/O (读文件、执行 git 命令), 但对外接口是纯的。
        """
        ...

    def describe(self) -> str:
        """返回传感器描述 (供调试/日志用)。"""
        ...