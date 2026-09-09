"""zall.core.rewind — 模型驱动上下文回滚 (kimi D-Mail 机制对标, 原创实现).

机制 (kimi soul/denwarenji.py + tools/dmail 逐行研读后按 zall 架构重写):
  - AgentLoop 每步开始时落一个可见的 [CHECKPOINT k] 锚点 (system 消息),
    并记录当时的消息列表长度 (仅当 context_rewind 工具已注册时, 零成本默认)。
  - 模型发现上下文里堆积了大量不再需要的内容 (读了大文件/搜了网页/调试弯路),
    可调用 context_rewind(checkpoint_id, message): 把上下文截回锚点处,
    并把 message 作为"给过去自己的信"追加 — 本质是把近期消息折叠成一条。
  - 单槽信箱: 一步内只允许一封; 无效 checkpoint_id 立即报错 (工具结果可见)。
  - 不回滚文件系统/外部状态; timeline 全量保留 (§6.1 可复现性不受影响)。

与 compaction 的关系: compaction 是系统被动触发的全量摘要;
rewind 是模型主动的精准折叠 — 两把独立的上下文管理武器。

IPR constraints:
  IPR-0: tests/test_context_rewind_invariants.py (含反例)
  IPR-3: stdlib only
"""

from __future__ import annotations

from dataclasses import dataclass


class RewindError(ValueError):
    """无效的回滚请求 (坏 checkpoint_id / 重复发送)。"""


@dataclass(frozen=True)
class RewindRequest:
    """一封"给过去自己的信"。"""

    checkpoint_id: int
    message: str


class RewindMailbox:
    """单槽回滚信箱 — 工具写入, AgentLoop 每步工具执行后取走。

    kimi DenwaRenji 对标: 单槽 (一步一封)、id 上界校验由 loop 每步同步。
    """

    __test__ = False

    def __init__(self) -> None:
        self._pending: RewindRequest | None = None
        self._n_anchors: int = 0

    @property
    def n_anchors(self) -> int:
        return self._n_anchors

    def set_n_anchors(self, n: int) -> None:
        """由 AgentLoop 在每次落锚后同步锚点总数。"""
        self._n_anchors = max(0, int(n))

    def request(self, checkpoint_id: int, message: str) -> None:
        """发起回滚请求 (由 context_rewind 工具调用)。

        Raises:
            RewindError: id 越界 / 消息为空 / 本步已有待处理请求。
        """
        if self._pending is not None:
            raise RewindError("a rewind is already pending for this step")
        if not (message or "").strip():
            raise RewindError("rewind message must not be empty — your past self "
                              "needs the distilled findings to continue")
        cid = int(checkpoint_id)
        if cid < 0:
            raise RewindError("checkpoint_id must be >= 0")
        if cid >= self._n_anchors:
            raise RewindError(
                f"no such checkpoint {cid} (existing: 0..{self._n_anchors - 1})"
                if self._n_anchors else "no checkpoints exist yet"
            )
        self._pending = RewindRequest(checkpoint_id=cid, message=message)

    def fetch(self) -> RewindRequest | None:
        """取走并清空待处理请求 (由 AgentLoop 调用)。"""
        pending = self._pending
        self._pending = None
        return pending

    def reset(self) -> None:
        """丢弃未处理请求并清零锚点计数 (新 loop 绑定时调用)。

        进程级 context_rewind 工具单例跨会话/跨 loop 共享同一 mailbox;
        上一个被中断的 loop 遗留的 _pending 若不清掉, 会被下一个 loop
        的第一步取走并按其锚点表截断新上下文 (跨会话串味, P1 fix)。
        """
        self._pending = None
        self._n_anchors = 0


__all__ = ["RewindError", "RewindMailbox", "RewindRequest"]
