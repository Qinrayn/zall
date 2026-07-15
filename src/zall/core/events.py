"""zall.core.events — EventBus: eventbus (DESIGN.md §6.1 呈现层投影增强).

Design:
  - EventBus 替代旧 observer 回调模式, 支持多个独立 listener 订阅不同事件
  - 每个 listener 接收 (kind, payload) 二元组
  - listener 异常被吞 (IPR-0 反例: 呈现层故障不得改变 RunEgress)
  - 向后兼容: AgentLoop 同时支持 observer 和 EventBus

O1 (写时复制):
  - listeners 以 tuple 存储, emit 时无锁读取
  - 注册/注销时通过 copy-on-write 替换整个 tuple, 写入操作加锁
  - 避免 emit 时长时间持有锁 (流式场景每秒数十次 emit)

Usage:
    bus = EventBus()
    bus.on("model_call", my_handler)
    bus.on("tool_call_start", my_other_handler)
    bus.emit("model_call", {"model": "..."})

    # Remove handler
    bus.off("model_call", my_handler)

    # Clear all handlers for an event
    bus.clear("model_call")

IPR constraints:
  IPR-0: listener 异常被吞 (呈现层故障不改变 RunEgress)
  IPR-3: stdlib only, no model SDK
"""

from __future__ import annotations

import threading
from typing import Any, Callable

# Listener sign: (kind: str, payload: dict) -> None
EventListener = Callable[[str, dict[str, Any]], None]


class EventBus:
    """eventbus — 多 listener 订阅/取消/broadcast。

    O1 写时复制: listeners 以 tuple 存储, emit 时在锁外执行 handler。
    线程安全: on/off/clear 使用 RLock, emit 无锁 (只读 tuple)。
    """

    __test__ = False

    def __init__(self) -> None:
        self._lock = threading.RLock()
        # O1: 使用 tuple (不可变), emit 时直接读, 无需锁
        # register/注销时复制整个list为新 tuple
        self._listeners: dict[str, tuple[EventListener, ...]] = {}

    def on(self, kind: str, handler: EventListener) -> None:
        """订阅event。相同 handler 重复register不会重复add。"""
        with self._lock:
            existing = self._listeners.get(kind, ())
            if handler not in existing:
                # O1: copy-on-write, replace为新的 tuple
                self._listeners[kind] = existing + (handler,)

    def off(self, kind: str, handler: EventListener) -> None:
        """取消订阅。若 handler 未register则静默ignore。"""
        with self._lock:
            existing = self._listeners.get(kind, ())
            if handler in existing:
                # O1: copy-on-write, remove handler
                self._listeners[kind] = tuple(h for h in existing if h is not handler)

    def clear(self, kind: str | None = None) -> None:
        """清除所有 listener。kind=None 时清除全部。"""
        with self._lock:
            if kind is None:
                self._listeners.clear()
            else:
                self._listeners.pop(kind, None)

    def emit(self, kind: str, payload: dict[str, Any] | None = None) -> None:
        """broadcastevent给所有订阅者。

        v2 fix: 在锁内获取 handlers 快照, 防止 dict rehash 竞态。
        旧实现直接读 _listeners dict 无锁, 在 on() 并发插入触发 rehash 时
        可能读到不一致状态。现在用锁获取 tuple 快照后释放, handler 执行
        仍在锁外 (防 handler 中 on/off 死锁)。

        通配符 `*` 监听所有事件类型。
        异常安全: 单个 listener 抛异常不影响其他 listener (IPR-0)。
        """
        # v2 fix: 锁内获取snapshot, 锁外execute handler
        with self._lock:
            handlers = self._listeners.get(kind, ())
            if kind != "*":
                wildcard = self._listeners.get("*", ())
                if wildcard:
                    handlers = handlers + wildcard
            # tuple 是不可变的, snapshot后释放锁是security的

        if not handlers:
            return
        payload = payload or {}
        for handler in handlers:
            try:
                handler(kind, payload)
            except Exception:
                # IPR-0: 呈现层故障不得影响语义
                pass

    @property
    def listener_count(self) -> int:
        """return当前register的 listener 总数 (调试用)。"""
        with self._lock:
            return sum(len(v) for v in self._listeners.values())

    @property
    def event_kinds(self) -> tuple[str, ...]:
        """return有 listener register的eventtype。"""
        with self._lock:
            return tuple(self._listeners.keys())