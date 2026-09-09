"""zall.core.notifications — 通知中心 (kimi NotificationManager 对标, 原创).

kimi notifications/manager.py 逐行研读后按 zall 单进程+线程架构重写。
保留其核心语义, 去掉文件存储 (zall 单进程, 内存 + 锁足够):

  pending ──claim()──▶ claimed ──ack()──▶ acked
              ▲            │
              └─recover()──┘  (claim 后超时未 ack → 归还 pending, 崩溃安全)

  - publish(dedupe_key=...) 幂等: 同 key 只入队一次 (kimi dedupe_key 对标)
  - deliver_pending(handler): claim → handler → ack;
    handler 异常 → 留在 claimed 等 recover 重投 (至少一次递送语义)

用途 (kimi background→notification→inject 闭环对标): 并行子代理完成后
publish 通知, 主 AgentLoop 每步开始时把待递送通知注入模型上下文 —
模型不再需要轮询 list_subagents 才知道后台结果。

root-only 语义: 只有显式 set_notification_center() 的 loop 才消费
(子代理 loop 不设 → 不误吞主 agent 的通知)。

IPR constraints:
  IPR-0: tests/test_notifications_invariants.py (含反例)
  IPR-3: stdlib only
"""

from __future__ import annotations

from typing import Any

import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field

# claim 后超过此秒数未 ack → recover 归还 pending (至少一次递送)
CLAIM_STALE_AFTER_S = 60.0
# 每步最多注入的通知数 (kimi limit=4 对标, 防单步上下文被通知刷爆)
DELIVER_LIMIT_PER_STEP = 4


@dataclass
class Notification:
    """一条通知 (后台任务完成/失败等)。"""

    title: str
    body: str = ""
    severity: str = "info"          # info | warning | error
    dedupe_key: str = ""
    id: str = field(default_factory=lambda: f"n{uuid.uuid4().hex[:8]}")
    # 递送状态: pending | claimed | acked
    status: str = "pending"
    claimed_at: float | None = None
    # 归属 run scope (P2 fix): 通知只被同 scope 的主 loop 消费;
    # None = 全局 (背向兼容: 无 scope 的 loop 收全部)。
    scope: str | None = None


class NotificationCenter:
    """线程安全的进程内通知中心 (claim/ack 至少一次递送)。

    P2 fix (跨 run 串味 + 无界增长):
      - publish 携带 scope; 带 scope 的 loop 只 claim 同 scope/全局条目
        (上一个 run 遗留的子代理通知不会注入下一个 run)。
      - ack 即删除条目 (不再永久停留在 _items)。
      - _items 总数有硬上限, 超限淘汰最旧条目。
    """

    __test__ = False

    # 条目硬上限: pending 长期无人消费 (例如端口 run 已死) 时防无界增长
    MAX_ITEMS = 500

    def __init__(self, *, claim_stale_after_s: float = CLAIM_STALE_AFTER_S) -> None:
        self._lock = threading.Lock()
        self._items: list[Notification] = []
        self._stale_after = claim_stale_after_s

    def publish(
        self, title: str, body: str = "", *,
        severity: str = "info", dedupe_key: str = "",
        scope: str | None = None,
    ) -> Notification:
        """发布通知; dedupe_key 命中已有条目时幂等返回旧条目。

        scope: 归属 run 标识 — 仅同 scope (或无 scope) 的 loop 消费。
        """
        with self._lock:
            if dedupe_key:
                for n in self._items:
                    if n.dedupe_key == dedupe_key:
                        return n
            # 硬上限: 淘汰最旧条目 (优先淘汰非 claimed, 不打断在途递送)
            if len(self._items) >= self.MAX_ITEMS:
                drop_idx = next(
                    (i for i, n in enumerate(self._items)
                     if n.status != "claimed"),
                    None,
                )
                if drop_idx is None:
                    drop_idx = 0
                del self._items[drop_idx]
            n = Notification(title=title, body=body,
                             severity=severity, dedupe_key=dedupe_key,
                             scope=scope)
            self._items.append(n)
            return n

    def recover(self) -> None:
        """把超时未 ack 的 claimed 归还 pending (崩溃/失败安全)。"""
        now = time.time()
        with self._lock:
            for n in self._items:
                if (n.status == "claimed" and n.claimed_at is not None
                        and now - n.claimed_at > self._stale_after):
                    n.status = "pending"
                    n.claimed_at = None

    def claim(
        self,
        limit: int = DELIVER_LIMIT_PER_STEP,
        scope: str | None = None,
    ) -> list[Notification]:
        """认领至多 limit 条 pending (先 recover 超时件)。

        scope 过滤: 带 scope 的 loop 只取同 scope 或全局 (scope=None) 条目;
        scope=None 的调用方取全部 (背向兼容)。
        """
        self.recover()
        now = time.time()
        claimed: list[Notification] = []
        with self._lock:
            for n in self._items:
                if n.status != "pending":
                    continue
                if scope is not None and n.scope not in (None, scope):
                    continue
                n.status = "claimed"
                n.claimed_at = now
                claimed.append(n)
                if len(claimed) >= limit:
                    break
        return claimed

    def ack(self, notification_id: str) -> None:
        """确认递送 — 条目即删 (不再驻留 _items, 有界)。"""
        with self._lock:
            self._items = [
                n for n in self._items if n.id != notification_id
            ]

    def deliver_pending(
        self,
        handler: Callable[[Notification], None],
        *, limit: int = DELIVER_LIMIT_PER_STEP,
        scope: str | None = None,
    ) -> list[Notification]:
        """claim → handler → ack; handler 异常 → 留 claimed 待 recover 重投。

        返回本次成功递送 (已 ack) 的通知列表。
        """
        delivered: list[Notification] = []
        for n in self.claim(limit=limit, scope=scope):
            try:
                handler(n)
            except Exception:
                continue  # 留在 claimed, 超时后 recover 重投 (至少一次)
            self.ack(n.id)
            delivered.append(n)
        return delivered

    @property
    def pending_count(self) -> int:
        with self._lock:
            return sum(1 for n in self._items if n.status == "pending")


# ── 进程级默认中心 (spawn_subagent 生产, 主 loop 消费) ──

_default_center: NotificationCenter | None = None
_default_lock = threading.Lock()


def get_notification_center() -> NotificationCenter:
    """进程级默认通知中心 (惰性单例)。"""
    global _default_center
    with _default_lock:
        if _default_center is None:
            _default_center = NotificationCenter()
        return _default_center


def reset_notification_center() -> None:
    """重置默认中心 (测试隔离用)。"""
    global _default_center
    with _default_lock:
        _default_center = None


# ── 主 loop 消费面 (协作者自由函数, 每步模型调用前) ──


def deliver_into_loop(loop: Any) -> int:
    """把待递送通知注入 loop 上下文 (system 消息 + timeline 记录)。

    仅当 loop 显式设置了 _notification_center 才消费 (root-only 语义;
    子代理 loop 不设 → 不误吞主 agent 的通知)。返回递送条数。
    """
    center = getattr(loop, "_notification_center", None)
    if center is None:
        return 0
    # P2 fix: 带 scope 的 loop 只收同 run 的通知 (子代理完成通知挂在 spawn 时
    # 的 run scope 上); 无 scope 的 loop 收全部 (背向兼容)。
    scope = getattr(loop, "_notification_scope", None)

    from zall.core.model import Message
    from zall.core.verifiability import EventType

    def _handler(n: Notification) -> None:
        text = f"[background notification] {n.title}"
        if n.body:
            text += "\n" + n.body
        loop._append_message(Message(role="system", content=text))
        loop._recorder.append(
            event_id=f"notify_{loop._step_count}_{n.id}",
            ts=int(time.time() * 1000),
            event_type=EventType.SYSTEM_INJECTION,
            payload={"reason": "notification", "title": n.title,
                     "severity": n.severity, "body": n.body[:200]},
        )

    return len(center.deliver_pending(_handler, scope=scope))


__all__ = [
    "CLAIM_STALE_AFTER_S",
    "DELIVER_LIMIT_PER_STEP",
    "Notification",
    "NotificationCenter",
    "deliver_into_loop",
    "get_notification_center",
    "reset_notification_center",
]
