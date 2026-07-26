"""zall.core.perception.world_model — WorldModel protocol (MASTER.md §4.2.3).

Corresponds to:
  §4.2.3  World Model: 预测行动后果, 从观测更新, 异常检测
  FR-2    感知不确定性: 预测结果必须携带置信度
  FR-0    统一性: World Model 抽象同时适用于具身和非具身

设计:
  WorldModel (Protocol)  — 世界模型接口
    - predict: (StateEstimate, Action) → StateEstimate (预测行动后果)
    - update:  (Observation) → None (从观测更新模型)
    - anomaly: (StateEstimate) → bool (异常检测)

  CodingWorldModel — 轻量级实现, 基于代码图 + 历史经验

IPR constraints:
  IPR-0: invariant tests at tests/test_perception_invariants.py
  IPR-3: pydantic / stdlib only, no model SDK
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from zall.core.perception.sensor import Observation, StateEstimate
from zall.core.action import Action


# ──────────────────────────────────────────────────────────────────────────
# WorldModel Protocol — 世界模型接口
# ──────────────────────────────────────────────────────────────────────────


@runtime_checkable
class WorldModel(Protocol):
    """世界模型接口 (MASTER.md §4.2.3)。

    世界模型是 agent 对世界的内部表征, 用于:
      1. 预测行动后果 (predict)
      2. 从观测更新模型 (update)
      3. 检测异常状态 (anomaly)

    实现级别:
      - 轻量级 (coding agent): 基于代码图 + 历史经验预测
      - 中量级 (科研 agent): 知识图谱 + 因果推理
      - 重量级 (具身 agent): 神经网络 latent dynamics + MPC

    IPR-0 不变量:
        - predict 返回的 StateEstimate 必须有 sources 包含 "world_model"
        - predict 不修改输入的 state
        - update 是幂等的 (相同 Observation 多次 update 不改变模型)
    """

    @property
    def model_id(self) -> str:
        """世界模型标识 (如 "coding_light_v1", "knowledge_graph_v2")。"""
        ...

    def predict(self, state: StateEstimate, action: Action) -> StateEstimate:
        """预测在给定状态下执行某个行动的后果。

        Args:
            state: 当前状态估计
            action: 要执行的行动

        Returns:
            预测后的状态估计 (带置信度)
        """
        ...

    def update(self, observation: Observation) -> None:
        """从观测更新世界模型内部状态。

        update 是 online 学习的基础——每次观测后更新模型,
        使未来的预测更准确。

        Args:
            observation: 新观测数据
        """
        ...

    def anomaly(self, state: StateEstimate) -> bool:
        """检测状态是否异常 (与模型预期不一致)。

        异常检测用于:
          - 感知系统故障检测
          - 环境变化检测 (coding: 外部修改了文件)
          - 安全监控 (具身: 碰撞检测)

        Args:
            state: 当前状态估计

        Returns:
            True 如果状态异常
        """
        ...


# ──────────────────────────────────────────────────────────────────────────
# NullWorldModel — 空世界模型 (默认实现)
# ──────────────────────────────────────────────────────────────────────────


class NullWorldModel:
    """空世界模型——不预测, 不更新, 不检测异常。

    作为默认实现, 当用户不提供世界模型时使用。
    所有预测返回输入状态的浅拷贝, 置信度不变。
    """

    __test__ = False  # 防 pytest 收集

    model_id: str = "null"

    def predict(self, state: StateEstimate, action: Action) -> StateEstimate:
        """空预测: 返回输入状态, 置信度不变。

        Counterexample: 期望世界模型预测但使用的是 NullWorldModel
                        → 预测结果与输入相同 (无变化, 合理降级)
        """
        return StateEstimate(
            state=dict(state.state),
            confidence=state.confidence,
            covariance=dict(state.covariance),
            sources=list(state.sources) + ["world_model"],
            timestamp=state.timestamp,
        )

    def update(self, observation: Observation) -> None:
        """空更新: 什么也不做。"""
        pass

    def anomaly(self, state: StateEstimate) -> bool:
        """空异常检测: 永远返回 False (无异常)。"""
        return False


# ──────────────────────────────────────────────────────────────────────────
# CompositeWorldModel — 组合多个世界模型
# ──────────────────────────────────────────────────────────────────────────


class CompositeWorldModel:
    """组合多个世界模型——按优先级顺序查询。

    高优先级模型先预测, 如果置信度足够高则使用其预测;
    否则 fallback 到低优先级模型。

    设计意图: 允许轻量级和重量级世界模型共存。
    (例如: 轻量级代码图预测 + 重量级神经网络预测)
    """

    __test__ = False  # 防 pytest 收集

    def __init__(self) -> None:
        self._models: list[WorldModel] = []
        self._confidence_threshold: float = 0.7

    @property
    def model_id(self) -> str:
        return f"composite({','.join(m.model_id for m in self._models)})"

    def add_model(self, model: WorldModel) -> None:
        """添加一个世界模型 (高优先级在前)。"""
        self._models.append(model)

    def predict(self, state: StateEstimate, action: Action) -> StateEstimate:
        """按优先级顺序预测, 返回第一个置信度足够的预测。

        如果所有模型置信度都不足, 返回最高置信度的预测。
        """
        if not self._models:
            # 无模型 → 返回输入状态 (与 NullWorldModel 一致)
            return StateEstimate(
                state=dict(state.state),
                confidence=state.confidence,
                covariance=dict(state.covariance),
                sources=list(state.sources) + ["world_model"],
                timestamp=state.timestamp,
            )

        best: StateEstimate | None = None
        for model in self._models:
            result = model.predict(state, action)
            if best is None or result.confidence > best.confidence:
                best = result
            if result.confidence >= self._confidence_threshold:
                return result

        return best or StateEstimate.empty()

    def update(self, observation: Observation) -> None:
        """所有模型并行更新。"""
        for model in self._models:
            model.update(observation)

    def anomaly(self, state: StateEstimate) -> bool:
        """任何模型检测到异常即返回 True。"""
        return any(model.anomaly(state) for model in self._models)