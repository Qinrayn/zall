"""zall.core.perception.engine — PerceptionEngine orchestrator (MASTER.md §4.2.3).

Corresponds to:
  §4.2.3  Perception Engine: 编排多传感器 → 融合 → 状态估计 → 世界模型预测
  FR-0    统一性: PerceptionEngine 同时适用于具身和非具身

设计:
  PerceptionEngine 是感知维度的编排器:
    1. 所有 Sensor.observe() → [Observation]
    2. 融合 → StateEstimate (带置信度)
    3. World Model 预测下一步
    4. 异常检测

  ContextManager 是"上下文窗口管理", PerceptionEngine 是"感知管道"。
  两者互补: PerceptionEngine 产生状态估计, ContextManager 管理模型看到的消息。

IPR constraints:
  IPR-0: invariant tests at tests/test_perception_invariants.py
  IPR-3: pydantic / stdlib only, no model SDK
"""

from __future__ import annotations

import time
from typing import Any

from zall.core.action import Action
from zall.core.perception.sensor import (
    Observation,
    Sensor,
    StateEstimate,
)
from zall.core.perception.world_model import (
    NullWorldModel,
    WorldModel,
)


class PerceptionEngine:
    """感知引擎编排器 (MASTER.md §4.2.3)。

    管理多传感器和世界模型, 提供统一的 perceive() 接口。

    Usage:
        engine = PerceptionEngine()
        engine.add_sensor(FileSensor())
        engine.add_sensor(GitSensor())
        engine.set_world_model(CodingWorldModel())

        # 执行感知
        estimate = engine.perceive()
        print(f"Current state: {estimate.state}")

        # 预测行动后果
        predicted = engine.predict(action)
        print(f"Predicted state: {predicted.state}")

        # 检查异常
        if engine.anomaly():
            print("Anomaly detected!")
    """

    __test__ = False  # 防 pytest 收集

    def __init__(self) -> None:
        self._sensors: dict[str, Sensor] = {}
        """注册的传感器 {sensor_id: Sensor}"""

        self._world_model: WorldModel = NullWorldModel()
        """当前世界模型 (默认 NullWorldModel)"""

        self._state: StateEstimate = StateEstimate.empty()
        """当前状态估计"""

        self._last_observations: dict[str, Observation] = {}
        """最近一次各传感器的观测 (用于调试/溯源)"""

    # ── Properties ──

    @property
    def sensors(self) -> dict[str, Sensor]:
        """已注册的传感器 (只读视图)。"""
        return dict(self._sensors)

    @property
    def world_model(self) -> WorldModel:
        """当前世界模型 (只读)。"""
        return self._world_model

    @property
    def state(self) -> StateEstimate:
        """当前状态估计 (只读)。"""
        return self._state

    @property
    def last_observations(self) -> dict[str, Observation]:
        """最近一次观测 (只读, 调试用)。"""
        return dict(self._last_observations)

    # ── Sensor management ──

    def add_sensor(self, sensor: Sensor) -> None:
        """注册一个传感器。

        Args:
            sensor: 实现 Sensor Protocol 的传感器实例

        Raises:
            ValueError: 如果 sensor_id 已存在
        """
        sid = sensor.sensor_id
        if sid in self._sensors:
            raise ValueError(
                f"sensor_id '{sid}' already registered. "
                f"Remove it first or use a different sensor."
            )
        self._sensors[sid] = sensor

    def remove_sensor(self, sensor_id: str) -> None:
        """移除一个传感器。

        Args:
            sensor_id: 要移除的传感器 ID
        """
        self._sensors.pop(sensor_id, None)

    def get_sensor(self, sensor_id: str) -> Sensor | None:
        """获取已注册的传感器。"""
        return self._sensors.get(sensor_id)

    def set_world_model(self, model: WorldModel) -> None:
        """设置世界模型。

        Args:
            model: 实现 WorldModel Protocol 的模型实例
        """
        self._world_model = model

    # ── Core perception pipeline ──

    def perceive(self) -> StateEstimate:
        """执行一次完整的感知循环。

        流程:
          1. 遍历所有传感器, 执行 observe()
          2. 收集 Observation, 记录到 _last_observations
          3. 融合多传感器观测 → StateEstimate
          4. 用世界模型更新内部状态
          5. 返回 StateEstimate

        Returns:
            StateEstimate — 当前状态估计
        """
        if not self._sensors:
            # 无传感器 → 返回空估计
            self._state = StateEstimate.empty()
            return self._state

        # 1. 执行所有传感器
        observations: list[Observation] = []
        for sid, sensor in self._sensors.items():
            try:
                obs = sensor.observe()
                self._last_observations[sid] = obs
                observations.append(obs)
            except Exception as _e:
                # 传感器故障 → 降级 (不阻塞整个感知循环)
                fallback = Observation.create(
                    sensor_id=sid,
                    data={},
                    confidence=0.0,
                    metadata={"error": str(_e)},
                )
                self._last_observations[sid] = fallback
                observations.append(fallback)

        # 2. 融合观测 → StateEstimate
        self._state = self._fuse(observations)

        # 3. 更新世界模型
        for obs in observations:
            try:
                self._world_model.update(obs)
            except Exception:
                pass  # 世界模型更新失败不阻塞

        return self._state

    def predict(self, action: Action) -> StateEstimate:
        """使用世界模型预测行动后果。

        Args:
            action: 要预测的行动

        Returns:
            预测后的状态估计
        """
        try:
            return self._world_model.predict(self._state, action)
        except Exception:
            # 预测失败 → 返回当前状态 (置信度降级)
            return StateEstimate(
                state=dict(self._state.state),
                confidence=self._state.confidence * 0.5,  # 降级
                covariance=dict(self._state.covariance),
                sources=list(self._state.sources) + ["world_model"],
                timestamp=int(time.time() * 1000),
            )

    def anomaly(self) -> bool:
        """检测当前状态是否异常。

        Returns:
            True 如果世界模型认为状态异常
        """
        try:
            return self._world_model.anomaly(self._state)
        except Exception:
            return False

    # ── State access ──

    def get_state_summary(self) -> dict[str, Any]:
        """返回状态摘要 (供日志/调试/注入 context 用)。

        Returns:
            dict 包含当前状态的关键信息
        """
        return {
            "sensors": list(self._sensors.keys()),
            "world_model": self._world_model.model_id,
            "state": dict(self._state.state),
            "confidence": self._state.confidence,
            "sources": list(self._state.sources),
            "anomaly": self.anomaly(),
        }

    # ── Internal ──

    def _fuse(self, observations: list[Observation]) -> StateEstimate:
        """融合多传感器观测为单一状态估计。

        融合策略:
          1. 合并所有 data 字典 (后注册的传感器可覆盖前面的键)
          2. 综合置信度 = 加权平均 (按各传感器置信度)
          3. 低置信度观测 (<0.3) 的键不参与融合

        Args:
            observations: 传感器观测列表

        Returns:
            融合后的 StateEstimate
        """
        if not observations:
            return StateEstimate.empty()

        # 合并状态 (只合并 confidence >= 0.3 的观测)
        fused_state: dict[str, Any] = {}
        total_weight = 0.0
        weighted_confidence = 0.0
        sources: list[str] = []
        covariance: dict[str, float] = {}

        for obs in observations:
            if obs.confidence < 0.3:
                # 低置信度观测: 不参与状态融合, 但仍记录来源
                sources.append(obs.sensor_id)
                covariance[obs.sensor_id] = 1.0 - obs.confidence
                continue

            # 高置信度: 参与融合
            fused_state.update(obs.data)
            sources.append(obs.sensor_id)
            weight = obs.confidence
            total_weight += weight
            weighted_confidence += obs.confidence * weight
            covariance[obs.sensor_id] = 1.0 - obs.confidence

        if total_weight > 0:
            final_confidence = weighted_confidence / total_weight
        else:
            final_confidence = 0.0

        return StateEstimate(
            state=fused_state,
            confidence=min(final_confidence, 1.0),
            covariance=covariance,
            sources=sources,
            timestamp=int(time.time() * 1000),
        )

    def reset(self) -> None:
        """重置感知引擎状态 (清空状态估计, 保留传感器注册)。

        C4 fix: 保留 _world_model (它是配置组件, 非瞬态状态)。旧实现把它替换为
        NullWorldModel(), 静默销毁用户经 set_world_model() 配置的世界模型。
        现在只清 _state 和 _last_observations。
        """
        self._state = StateEstimate.empty()
        self._last_observations.clear()