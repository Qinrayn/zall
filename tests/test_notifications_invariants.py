"""通知中心 (NotificationCenter, kimi claim/ack 至少一次递送对标) 不变量测试.

IPR-0: each test must contain a counterexample.

Protected invariants:
  I-NOTIFY-1: 状态机 pending→claimed→acked; deliver 成功即 ack, 不重复递送。
  I-NOTIFY-2: 至少一次递送 — handler 异常 → 留 claimed; 超时 recover 归还
              pending 后重投 (反例: 未超时不归还)。
  I-NOTIFY-3: dedupe_key 幂等 — 同 key 二次 publish 不产生新条目。
  I-NOTIFY-4: root-only 消费 — 未设通知中心的 loop 零注入 (反例孪生:
              设了中心的 loop 下一步收到 system 消息 + timeline 记录)。
  I-NOTIFY-5: 并行子代理完成 → 自动发布通知 (spawn_subagent 生产面闭环)。
"""

from __future__ import annotations

import time

import pytest

from zall.core.notifications import (
    Notification,
    NotificationCenter,
    deliver_into_loop,
    get_notification_center,
    reset_notification_center,
)


@pytest.fixture(autouse=True)
def _isolate_default_center():
    reset_notification_center()
    yield
    reset_notification_center()


# ── I-NOTIFY-1: 状态机 + 不重复递送 ──


def test_deliver_acks_and_does_not_redeliver() -> None:
    center = NotificationCenter()
    center.publish("task done", "all good")
    got: list[Notification] = []
    delivered = center.deliver_pending(got.append)
    assert len(delivered) == 1 and got[0].title == "task done"
    # 反例: 已 ack 不重复递送
    again: list[Notification] = []
    assert center.deliver_pending(again.append) == []
    assert again == []
    assert center.pending_count == 0


def test_deliver_limit_respected() -> None:
    center = NotificationCenter()
    for i in range(6):
        center.publish(f"t{i}")
    got: list[Notification] = []
    center.deliver_pending(got.append, limit=4)
    assert len(got) == 4
    assert center.pending_count == 2


# ── I-NOTIFY-2: 至少一次递送 ──


def test_failed_handler_leaves_claimed_then_recovers() -> None:
    center = NotificationCenter(claim_stale_after_s=0.05)
    center.publish("fragile")

    def _boom(n: Notification) -> None:
        raise RuntimeError("handler crashed")

    assert center.deliver_pending(_boom) == []      # 失败 → 未 ack
    # 反例: 未超时前不归还 (不会立刻重复投递)
    got: list[Notification] = []
    assert center.deliver_pending(got.append) == []
    # 超时后 recover → 重投成功 (至少一次)
    time.sleep(0.06)
    delivered = center.deliver_pending(got.append)
    assert len(delivered) == 1 and got[0].title == "fragile"


# ── I-NOTIFY-3: dedupe 幂等 ──


def test_dedupe_key_idempotent() -> None:
    center = NotificationCenter()
    a = center.publish("done", dedupe_key="task_1")
    b = center.publish("done again", dedupe_key="task_1")
    assert a.id == b.id                              # 同条目
    got: list[Notification] = []
    center.deliver_pending(got.append)
    assert len(got) == 1                             # 反例: 不双投
    # 无 dedupe_key 的两条各自独立
    center.publish("x")
    center.publish("x")
    assert center.pending_count == 2


# ── I-NOTIFY-4: root-only 消费 (loop 注入面) ──


class _FakeLoop:
    __test__ = False

    def __init__(self) -> None:
        self.messages: list = []
        self._step_count = 1
        self._notification_center = None
        self.timeline: list[dict] = []
        self._recorder = self

    def append(self, **kw) -> None:
        self.timeline.append(kw.get("payload", {}))

    def _append_message(self, m) -> None:
        self.messages.append(m)


def test_loop_without_center_gets_nothing() -> None:
    get_notification_center().publish("orphan")
    loop = _FakeLoop()                               # 未设中心 (子代理语义)
    assert deliver_into_loop(loop) == 0
    assert loop.messages == []


def test_loop_with_center_receives_injection() -> None:
    """反例孪生: 设了中心 → system 消息 + timeline 记录。"""
    center = get_notification_center()
    center.publish("subagent abc completed", "output preview")
    loop = _FakeLoop()
    loop._notification_center = center
    assert deliver_into_loop(loop) == 1
    assert len(loop.messages) == 1
    m = loop.messages[0]
    assert m.role == "system"
    assert "background notification" in m.content
    assert "subagent abc completed" in m.content
    assert any(p.get("reason") == "notification" for p in loop.timeline)
    # 再次递送为空 (已 ack)
    assert deliver_into_loop(loop) == 0


# ── I-NOTIFY-5: spawn_subagent 生产面 ──


def test_parallel_subagent_completion_publishes_notification() -> None:
    """并行子代理完成 → 进程级中心收到通知 (dedupe by sub_id)。"""
    from zall.tools.spawn_subagent import SpawnSubagentTool
    tool = SpawnSubagentTool()

    # 注入一个立即返回的假模型 (不走网络)
    from zall.core.model import ModelResponse, StopReason

    class _InstantAdapter:
        __test__ = False
        model_name = "fake-instant"

        def complete(self, messages, tools, tool_choice=None):
            return ModelResponse(content="sub done", stop_reason=StopReason.STOP)

    from zall.core.safety import RuleSet
    from zall.core.tool import ToolRegistry
    tool.set_context(_InstantAdapter(), ToolRegistry(), RuleSet())

    result = tool.execute({"prompt": "analyze something quickly",
                           "parallel": True})
    assert result.success
    sub_id = result.artifacts["subagent_id"]
    # 等后台线程完成回调发布通知
    center = get_notification_center()
    for _ in range(100):
        if center.pending_count > 0:
            break
        time.sleep(0.05)
    got: list[Notification] = []
    center.deliver_pending(got.append)
    assert got, "subagent completion must publish a notification"
    assert sub_id in got[0].title
    tool.close()


# ── I-NOTIFY-6: scope 隔离 (P2 回归: 跨 run 串味) ──


def test_scope_isolates_runs() -> None:
    """A run 的通知不得注入 B run (scope 不匹配 → 不消费)。"""
    center = NotificationCenter()
    center.publish("run A finished", scope="run-a")

    consumed: list[Notification] = []
    assert len(center.deliver_pending(consumed.append, scope="run-a")) == 1
    assert consumed[0].title.startswith("run A")
    center.publish("run B news", scope="run-b")
    got_b: list[Notification] = []
    assert len(center.deliver_pending(got_b.append, scope="run-b")) == 1
    assert got_b[0].title == "run B news"
    # 反例: scope=run-c 拿不到已消费的 (b 已全收走)
    assert center.deliver_pending(lambda _n: None, scope="run-c") == []


def test_scoped_loop_also_sees_global_notifications() -> None:
    """全局 (scope=None) 通知对任何 scope 可见 — 不回滚老发布方。"""
    center = NotificationCenter()
    center.publish("global event")
    got: list[Notification] = []
    assert len(center.deliver_pending(got.append, scope="run-x")) == 1
    assert got[0].title == "global event"


def test_scopeless_consumer_gets_all() -> None:
    """无 scope 的 loop 收全部 (背向兼容) — 含带 scope 的通知。"""
    center = NotificationCenter()
    center.publish("scoped", scope="run-a")
    center.publish("plain")
    got: list[Notification] = []
    assert len(center.deliver_pending(got.append)) == 2  # scope=None 不过滤


# ── I-NOTIFY-7: ack 即删 + 硬上限 (P2 回归: 无界增长) ──


def test_ack_removes_entry_not_just_status() -> None:
    """ack 后条目从 _items 删除: 条数归零, dedupe key 可复用为新条目。"""
    center = NotificationCenter()
    n = center.publish("done", dedupe_key="k")
    center.deliver_pending(lambda _n: None)
    assert len(center._items) == 0  # 反例: 驻留即泄漏
    m = center.publish("done again", dedupe_key="k")
    assert m.id != n.id  # 同 key 再入队是新条目 (旧条目已删)


def test_items_capped_at_max() -> None:
    """硬上限 MAX_ITEMS — 超发不无界增长。"""
    center = NotificationCenter()
    for i in range(center.MAX_ITEMS + 50):
        center.publish(f"t{i}")
    assert len(center._items) <= center.MAX_ITEMS
    assert center.pending_count <= center.MAX_ITEMS


def test_claimed_items_not_dropped_when_cap_reached() -> None:
    """淘汰优先选非 claimed — 在途递送不被打断 (反例: claimed 被删即丢信)。"""
    center = NotificationCenter()
    n1 = center.publish("in flight")
    center.claim(limit=1)  # n1 → claimed
    for i in range(center.MAX_ITEMS):
        center.publish(f"t{i}")
    assert len(center._items) <= center.MAX_ITEMS
    ids = {n.id for n in center._items}
    assert n1.id in ids, "in-flight claimed must survive the cap trim"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
