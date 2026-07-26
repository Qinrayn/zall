"""zall.core.perception — Perception Engine (MASTER.md §4.2.3).

Corresponds to:
  §4.2.3  Perception Engine: 传感器 → Observation → Percept → StateEstimate → World Model
  FR-2    感知不确定性: 所有感知必须携带置信度 (confidence ∈ [0,1])
  FR-0    统一性: 具身和非具身共享同一 Perception 抽象

本包是**感知维度的核心抽象**——定义 Sensor / Observation / Percept / StateEstimate /
WorldModel / PerceptionEngine 的统一接口。

IPR constraints:
  IPR-0: invariant tests at tests/test_perception_invariants.py
  IPR-1: corresponds to MASTER.md §4.2.3
  IPR-3: pydantic / stdlib only, no model SDK
  IPR-4: this file is a primitive, no main Loop

Usage:
    from zall.core.perception import (
        Sensor,
        Observation,
        Percept,
        StateEstimate,
        WorldModel,
        PerceptionEngine,
    )

    engine = PerceptionEngine()
    engine.add_sensor(FileSensor())
    engine.add_sensor(GitSensor())
    estimate = engine.perceive()
    print(f"State: {estimate.state}, confidence: {estimate.confidence}")
"""

from __future__ import annotations

from .sensor import Sensor, Observation, Percept, StateEstimate
from .world_model import WorldModel
from .engine import PerceptionEngine

__all__ = [
    "Sensor",
    "Observation",
    "Percept",
    "StateEstimate",
    "WorldModel",
    "PerceptionEngine",
]