"""zall.core.provenance — ScienceProvenance (E3 Science Kit, docs/E3_SCIENCE_KIT.md §2.4).

Corresponds to:
  docs/E3_SCIENCE_KIT.md §2.4  ScienceProvenance — 实验 protocol + data + code + env 的完整血缘
  MASTER.md §1.5               HypothesisGoal 作为 Commitment 扩展
  MASTER.md §3.3               评估体系 — 实验完成判定
  MASTER.md §10 I-10           负结果平等 — 负结果与正结果同等珍视

IPR constraints:
  IPR-3: only pydantic / stdlib, no model SDK
"""

from __future__ import annotations

import hashlib
import time

from pydantic import BaseModel

from zall.core.verifiability import (
    AckEvent,
    EventType,
    RunRecorder,
)


class _ScienceAnchor:
    """TrustAnchor 适配器: 用 provenance hash 作为锚点标识 (E3_SCIENCE_KIT.md §2.4).

    RunRecorder.anchor_to() 需要 TrustAnchor 协议, 但 ScienceProvenance 没有外部
    签名服务。此适配器将 provenance hash 编码到 AckEvent.sig 中, 使 provenance
    数据进入 timeline 的链式哈希, 复用 Verifiability 维度的可复现能力。

    适配层: 不改 verifiability.py, 在 provenance 侧做适配。
    """

    def __init__(self, provenance_hash: str) -> None:
        self._prov_hash = provenance_hash
        self._anchor_id = f"science_{provenance_hash[:16]}"

    @property
    def anchor_id(self) -> str:
        return self._anchor_id

    def write_run_tail(
        self, run_id: str, last_event_hash: str, ts: int
    ) -> AckEvent:
        """将 provenance hash 编码到 sig 字段, 返回 AckEvent。

        sig 格式: "science_prov_<provenance_sha256>"
        provenance hash 因此进入 timeline 的 ANCHOR_ACK 事件 payload,
        被链式哈希保护, 不可篡改。
        """
        return AckEvent(
            anchor_id=self.anchor_id,
            run_id=run_id,
            last_event_hash=last_event_hash,
            ts=ts,
            sig=f"science_prov_{self._prov_hash}",
        )


class ScienceProvenance(BaseModel):
    """实验 protocol + data + code + env 的完整血缘 (E3_SCIENCE_KIT.md §2.4).

    接入 Verifiability timeline: 通过 anchor_to() 把 provenance 锚定到 RunRecorder,
    复用 Verifiability 维度的链式哈希 + 可复现能力, 不重造轮子。

    字段:
        protocol_hash:      实验脚本 hash
        data_hash:          输入数据 hash
        analysis_code_hash: 分析代码 hash
        environment_hash:   env snapshot hash (requirements.txt + python version)
        lineage:            衍生自哪些 prior experiment 的 UUID 列表
        timeline_anchor:    RunRecorder.anchor_to() 返回的 anchor event_id
    """

    protocol_hash: str
    data_hash: str
    analysis_code_hash: str
    environment_hash: str
    lineage: list[str] = []
    timeline_anchor: str | None = None  # 接 RunRecorder.anchor_to() 的返回

    def anchor_to(self, recorder: RunRecorder) -> str:
        """把 provenance 锚定到 RunRecorder timeline, 返回 anchor event_id。

        适配: recorder.anchor_to() 需要 TrustAnchor + ts, 但 ScienceProvenance
        需要把自身 hash 送进 timeline。策略:
          1. 序列化 self, 算 SHA-256 hash
          2. 把 provenance hash 追加为 timeline event (进入链式哈希)
          3. 调 recorder.anchor_to() 锚定 (此时 tail 已含 provenance)
          4. record 返回的 anchor event_id 到 self.timeline_anchor

        返回:
            anchor event_id (非空字符串)
        """
        prov_data = self.model_dump_json()
        prov_hash = hashlib.sha256(prov_data.encode("utf-8")).hexdigest()
        ts = int(time.time() * 1000)

        # 1. 把 provenance hash 记录到 timeline (成为链尾, 被链式哈希保护)
        recorder.append(
            event_id=f"prov_{prov_hash[:16]}_{ts}",
            ts=ts,
            event_type=EventType.ANCHOR_ACK,
            payload={"provenance_hash": prov_hash},
        )

        # 2. 调用 recorder.anchor_to() 锚定 (此时 tail 已包含 provenance)
        anchor = _ScienceAnchor(prov_hash)
        result = recorder.anchor_to(anchor, ts)
        if result is not None:
            self.timeline_anchor = result.event_id
        return self.timeline_anchor or ""