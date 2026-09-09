"""zall.core.verifiability — RunRecorder + TrustAnchor (DESIGN.md §6.1 + §6.5.2).

Corresponds to:
  §6.1     RunRecorder: append-only timeline + 链式哈希 + 事件先于行动
  §6.5.2   TrustAnchor: ed25519 签名 + 承诺边界 + out-of-band 初始化

承诺边界 (§6.5.2.2, 显式声明, 不假装 adversary-resilient):
    ✅ agent 进程篡改可发现
    ✅ 同 OS user 启动的其他非 root 进程篡改可发现
    ❌ 同 OS user 本人主动篡改 (须远程/硬件 token, 本轮不推)
    ❌ OS root 篡改 (同上)

v0.0.10: FileTrustAnchor 实现 (ed25519 签名 + 文件追加锚点日志)

IPR constraints:
  IPR-0: invariant tests at tests/test_verifiability_invariants.py, includesCounterexample
  IPR-1: this file corresponds to DESIGN.md §6.1 + §6.5.2
  IPR-3: only pydantic / stdlib / cryptography, no model SDK
  IPR-4: this file is a primitive, no main Loop
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from collections.abc import Iterator
from enum import Enum
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from pydantic import BaseModel, ConfigDict

_log = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────
# §6.1 EventType (eventtype)
# ──────────────────────────────────────────────────────────────────────────


class EventType(str, Enum):
    """timeline eventtype (DESIGN.md §6.1)。

    v0.0.5 加了 anchor_ack (外部锚点签回的事件)。
    v0.0.10 加了 context_compaction (上下文压缩事件, §9.2.9)。
    v0.0.11 加了 goal_downgrade (Goal 降级, §3.4) + pr0_hallucination (PR-0 幻觉检测)。
    """

    MODEL_CALL = "model_call"
    TOOL_CALL_START = "tool_call_start"
    TOOL_CALL_END = "tool_call_end"
    GATE_DECISION = "gate_decision"
    USER_RESPONSE = "user_response"
    OVERRIDE = "override"
    JUDGE_RESULT = "judge_result"
    ANCHOR_ACK = "anchor_ack"
    CONTEXT_COMPACTION = "context_compaction"
    CONTEXT_REWIND = "context_rewind"      # 模型主动上下文回滚 (kimi D-Mail 对标)
    GOAL_DOWNGRADE = "goal_downgrade"
    PR0_HALLUCINATION = "pr0_hallucination"
    SYSTEM_INJECTION = "system_injection"  # v0.0.22: 系统注入消息 (eg. 空 STOP nudge), 守 §6.1 全保真
    # Phase 1 (修裂缝): goal lifecycle events (§3.2 + §9.2.1)
    GOAL_STATEMENT = "goal_statement"   # Goal 被锁定并记录到 timeline
    USER_CONFIRM = "user_confirm"        # 用户确认 Goal (confirm gate 通过)
    # §12.3 E1.1: 感知异常事件 (perception anomaly detected, 含状态摘要)
    PERCEPTION_ANOMALY = "perception_anomaly"
    # E4: user interrupt (Ctrl+C during step loop)
    USER_INTERRUPT = "user_interrupt"     # 用户中断, model 半成品被丢弃
    # §12.1 Verifiability: 链完整性自检失败 (运行时检测篡改, 不阻止运行)
    CHAIN_BROKEN = "chain_broken"


# ──────────────────────────────────────────────────────────────────────────
# §6.1 TimelineEvent (单条event + chain hash)
# ──────────────────────────────────────────────────────────────────────────


class TimelineEvent(BaseModel):
    """timeline 中的一条event (DESIGN.md §6.1)。

    IPR-0 不变量:
        - frozen (append-only: 一旦写入immutable)
        - prev_hash 链式: 每条includes前一条的 SHA-256 hash
        - event_id 唯一 (uuid)
        - "意图先于行动": tool_call_start 必须在 tool_call_end 之前
          (时序constraints由调用方保证, RunRecorder 只保证链不断)

    链式哈希:
        event_hash = SHA-256(event_id || ts || event_type || payload_json || prev_hash)
        首条的 prev_hash = "0" * 64 (genesis)
    """

    model_config = ConfigDict(frozen=True)

    event_id: str
    ts: int  # unix timestamp (毫秒)
    event_type: EventType
    payload: dict[str, Any] = {}
    prev_hash: str = "0" * 64  # genesis prev_hash

    def compute_hash(self) -> str:
        """计算本条event的 SHA-256 hash。

        纯函数: 不依赖外部状态, 相同输入相同输出 (幂等性)。
        """
        data = json.dumps(
            {
                "event_id": self.event_id,
                "ts": self.ts,
                "event_type": self.event_type.value,
                "payload": self.payload,
                "prev_hash": self.prev_hash,
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha256(data.encode("utf-8")).hexdigest()

    @staticmethod
    def __no_tool_history__() -> bool:
        """TimelineEvent 是"event记录", 不是"tool 调用历史"的回灌源。

        §4.3 核心斩断: Context 不许includes tool 历史。
        timeline 是 Verifiability 的审计轨迹, 不是 Context 的输入。
        """
        return True


# ──────────────────────────────────────────────────────────────────────────
# §6.5.2 TrustAnchor (外部可信anchor)
# ──────────────────────────────────────────────────────────────────────────


class TrustAnchorInit(BaseModel):
    """anchorinit化数据 (DESIGN.md §6.5.2.3)。

    out-of-band 一次性建立, 之后不可重写。
    user 通过对比此处 public_key_fp / ts_init 与 timeline 内首条 anchor_ack 的签名,
    验证锚点未被 silent 替换。

    IPR-0 不变量:
        - frozen
        - public_key_fp 非空 (ed25519 公钥指纹)
    """

    model_config = ConfigDict(frozen=True)

    anchor_id: str
    public_key_fp: str  # ed25519 公钥的 SHA-256 指纹 (hex)
    ts_init: int
    anchor_software_version: str = "0.0.1"


class AckEvent(BaseModel):
    """anchor签字产物 (DESIGN.md §6.5.2.4)。

    TrustAnchor.write_run_tail 返回此结构, 写回 timeline 作为一条 ANCHOR_ACK 事件。

    签名内容: sign(private_key, last_event_hash || ts || run_id)

    IPR-0 不变量:
        - frozen
        - sig 非空 (ed25519 签名, hex)
        - last_event_hash 是 hex 64 字符 (SHA-256)
    """

    model_config = ConfigDict(frozen=True)

    anchor_id: str
    run_id: str
    last_event_hash: str
    ts: int
    sig: str  # ed25519 签名 (hex)
    prev_anchor_hash: str = "0" * 64  # 锚点自身链式 (genesis)


@runtime_checkable
class TrustAnchor(Protocol):
    """外部可信anchorprotocol (DESIGN.md §6.5.2)。

    最小接口: 只暴露 write_run_tail, 只签收到的 hash 并写自己的 log。
    不读 timeline 内容, 不验证语义, 不调外部服务 (§6.5.2.4 最小化)。

    承诺边界 (§6.5.2.2):
        ✅ agent 进程篡改可发现
        ❌ 不防 user 本人 / OS root

    ed25519 纯密码学, 不依赖模型 (守 PR-3)。
    """

    @property
    def anchor_id(self) -> str: ...

    def write_run_tail(
        self, run_id: str, last_event_hash: str, ts: int
    ) -> AckEvent: ...


# ──────────────────────────────────────────────────────────────────────────
# §6.1 RunRecorder (memoryversion, file持久化 deferred)
# ──────────────────────────────────────────────────────────────────────────


class RunRecorder:
    """timeline 记录器 (DESIGN.md §6.1)。

    内存版: 维护 event list + 链式哈希, 可验证链完整性。
    文件持久化 (append-only mode, OS 级) deferred —— 不影响核心不变量。

    M-fix (内存有界): 可选 spill_dir 开启"内存窗口 + 磁盘全量"模式 —
      超过 max_memory_events 条时, 最旧的 event 序列化追加到
      <spill_dir>/<run_id>/timeline.spill.jsonl 并从内存弹出。
      链完整性不受影响: 被弹出的条目哈希仍被后一条的 prev_hash 引用,
      首条被弹出事件保持 genesis prev_hash, 整链从头到尾仍可验证。
      events()/iter_events() 会把磁盘头 + 内存尾拼回完整序列。
      未开启 spill 时行为与旧内存版完全一致。

    IPR-0 不变量:
        - append 后immutable (event 是 frozen pydantic)
        - 链不断: 每条 prev_hash == 前一条 compute_hash()
        - 首条 prev_hash == "0"*64 (genesis)

    承诺边界 (§6.5.2.2):
        篡改可发现 (agent 进程级), 不防 user 本人 / OS root。
    """

    def __init__(
        self,
        run_id: str,
        *,
        spill_dir: str | Path | None = None,
        max_memory_events: int = 2000,
    ) -> None:
        self._run_id = run_id
        self._events: list[TimelineEvent] = []
        self._events_cache: tuple[TimelineEvent, ...] | None = None
        self._max_memory_events = max(1, int(max_memory_events))
        # spill (M-fix): 内存窗口 + 磁盘全量
        self._spill_path: Path | None = None
        self._spill_fp: Any = None
        self._spilled_count = 0
        self._spill_tail_hash: str | None = None  # 全在盘上时的链尾 hash
        if spill_dir is not None:
            self._spill_path = Path(spill_dir) / run_id / "timeline.spill.jsonl"

    def _ensure_spill_open(self) -> Any:
        """惰性打开 spill 文件 (首次真正溢出时才落盘/建目录)。"""
        if self._spill_fp is None and self._spill_path is not None:
            self._spill_path.parent.mkdir(parents=True, exist_ok=True)
            self._spill_fp = open(self._spill_path, "a", encoding="utf-8")
        return self._spill_fp

    def close(self) -> None:
        """关闭 spill 文件句柄 (进程退出/callback 清理; 幂等)。"""
        if self._spill_fp is not None:
            try:
                self._spill_fp.close()
            finally:
                self._spill_fp = None

    def __del__(self) -> None:  # noqa: D105 (GC 兜底关句柄)
        try:
            self.close()
        except Exception:
            pass

    # ── spill 序列化 (与 cli/session.py timeline.jsonl 同构) ──

    @staticmethod
    def _event_to_dict(ev: TimelineEvent) -> dict[str, Any]:
        return {
            "event_id": ev.event_id,
            "ts": ev.ts,
            "event_type": ev.event_type.value,
            "payload": ev.payload,
            "prev_hash": ev.prev_hash,
            "hash": ev.compute_hash(),
        }

    @staticmethod
    def _event_from_dict(d: dict[str, Any]) -> TimelineEvent:
        d.pop("hash", None)  # 读取端忽略冗余 hash 字段
        return TimelineEvent(**d)

    def _spill_oldest(self) -> None:
        """把最旧的一条 event 序列化落盘并从内存弹出 (链不断)。

        被弹出事件的 prev_hash 本来就是后一条的 prev_hash 引用值,
        首条 (genesis) 落盘后其后续链不变 — 无需任何重锚定。
        """
        fp = self._ensure_spill_open()
        if fp is None:
            # 未配置 spill: 内存版无界 (与旧行为一致)
            return
        last_spilled: TimelineEvent | None = None
        while self._events and len(self._events) > self._max_memory_events:
            ev = self._events.pop(0)
            fp.write(json.dumps(
                self._event_to_dict(ev), ensure_ascii=False) + "\n")
            self._spilled_count += 1
            last_spilled = ev
        if last_spilled is not None:
            fp.flush()
            self._events_cache = None
            if not self._events:
                # 罕见的全量外溢: 链尾落到盘上, 供 tail_hash 兜底
                self._spill_tail_hash = last_spilled.compute_hash()

    @property
    def run_id(self) -> str:
        return self._run_id

    @property
    def spilled_count(self) -> int:
        """已落盘 (从内存弹出) 的 event 条数。"""
        return self._spilled_count

    @property
    def spill_path(self) -> Path | None:
        return self._spill_path

    def __len__(self) -> int:
        """总 event 条数 (盘 + 内存), 不物化全量。"""
        return self._spilled_count + len(self._events)

    def _iter_spilled(self) -> Iterator[TimelineEvent]:
        """按序产出盘上 spilled 事件 (仅当 spill 启用且有内容)。"""
        if self._spill_path is None or self._spilled_count == 0:
            return
        if not self._spill_path.exists():
            return
        try:
            with open(self._spill_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    yield self._event_from_dict(json.loads(line))
        except (OSError, ValueError, json.JSONDecodeError):
            _log.warning(
                "timeline spill file unreadable (run %s); chain reduced to memory window",
                self._run_id,
            )
            return

    def iter_events(self) -> Iterator[TimelineEvent]:
        """按序产出全部事件 (盘头 + 内存尾), 流式不物化。"""
        yield from self._iter_spilled()
        yield from self._events

    @property
    def events(self) -> tuple[TimelineEvent, ...]:
        """所有已记录event (read-only视图, O7 cache)。

        内存版: 缓存的 tuple; spill 版: 物化拼接 (调用方按需一次取全)。
        """
        if self._spilled_count == 0:
            if self._events_cache is None:
                self._events_cache = tuple(self._events)
            return self._events_cache
        return tuple(self.iter_events())

    @property
    def tail_hash(self) -> str:
        """当前链尾 hash。无event时return genesis hash。"""
        if self._events:
            return self._events[-1].compute_hash()
        if self._spill_tail_hash is not None:
            return self._spill_tail_hash
        return "0" * 64

    def append(
        self,
        event_id: str,
        ts: int,
        event_type: EventType,
        payload: dict[str, Any] | None = None,
    ) -> TimelineEvent:
        """追加一条event, 自动计算 prev_hash。

        返回构造好的 TimelineEvent (frozen, immutable)。
        """
        prev = self.tail_hash
        event = TimelineEvent(
            event_id=event_id,
            ts=ts,
            event_type=event_type,
            payload=payload or {},
            prev_hash=prev,
        )
        self._events.append(event)
        self._events_cache = None  # O7: invalidate cache
        if len(self._events) > self._max_memory_events:
            self._spill_oldest()
        return event

    def verify_chain(self) -> bool:
        """validate链完整性。

        纯函数: 不修改状态, 不调外部服务。
        逐条检查 prev_hash == 前一条 compute_hash()。
        首条 prev_hash == "0"*64 (genesis)。

        spill 版先验盘上头部 (genesis 起), 再续验内存窗口 —
        被弹出事件仍链式引用, 整链从头到尾可验证。

        Counterexample: 如果有人篡改了某条事件的 payload, 该条 compute_hash() 变化,
        但下一条的 prev_hash 仍是旧值 → 不匹配 → verify_chain  False。
        """
        prev = "0" * 64
        if self._spilled_count:
            for event in self._iter_spilled():
                if event.prev_hash != prev:
                    return False
                prev = event.compute_hash()
        for event in self._events:
            if event.prev_hash != prev:
                return False
            prev = event.compute_hash()
        return True

    def anchor_to(self, anchor: TrustAnchor, ts: int) -> TimelineEvent | None:
        """把当前链尾 hash commit给 TrustAnchor 签字, ack 写回 timeline。

        §6.5.2.5: RunRecorder → anchor → ack → RunRecorder 闭环。
        无事件时不提交 (空链无意义)。
        """
        if not self._events and self._spilled_count == 0:
            return None
        ack = anchor.write_run_tail(self._run_id, self.tail_hash, ts)
        # ack 写回 timeline 作为 ANCHOR_ACK event
        # B8 fix: add counter 防同毫秒 event_id 冲突
        self._anchor_ack_counter = getattr(self, '_anchor_ack_counter', 0) + 1
        event = self.append(
            event_id=f"anchor_ack_{ack.ts}_{self._anchor_ack_counter}",
            ts=ack.ts,
            event_type=EventType.ANCHOR_ACK,
            payload={
                "anchor_id": ack.anchor_id,
                "run_id": ack.run_id,
                "last_event_hash": ack.last_event_hash,
                "sig": ack.sig,
                "prev_anchor_hash": ack.prev_anchor_hash,
            },
        )
        return event


# ──────────────────────────────────────────────────────────────────────────
# §6.5.2 FileTrustAnchor — ed25519 sign + file追加anchorlog (v0.0.10)
# ──────────────────────────────────────────────────────────────────────────

_ANCHOR_KEY_FILE = ".zall/trust_anchor_key"  # ed25519 私钥 (PEM, 仅 owner 读写)
_ANCHOR_LOG_FILE = ".zall/trust_anchor.log"   # 追加锚点日志 (chmod 0600)
_ANCHOR_INIT_FILE = ".zall/trust_anchor_init.txt"  # out-of-band 初始化指纹
# 锚点日志上限: 超过此条数时淘汰最旧的 1/3 (防日志无限增长拖慢 _read_last_anchor_hash)
_ANCHOR_LOG_MAX_ENTRIES = 500


class FileTrustAnchor:
    """ed25519 signanchor — 满足 TrustAnchor Protocol (DESIGN.md §6.5.2)。

    设计:
      - 私钥存储为 PEM 格式, 目录权限尽量设 0700 / 文件 0600
      - 首次初始化时生成 out-of-band 指纹文件供用户验证
      - anchor log 是追加式日志, 每条含签名 + prev_anchor_hash (自身链式)

    承诺边界:
      ✅ agent 进程篡改可发现 (锚点日志独立于 timeline 目录)
      ✅ 同 OS user 其他非 root 进程篡改可发现
      ❌ 同 OS user 本人 / OS root 篡改 (见 §6.5.2.2)

    IPR-3: ed25519 纯密码学, 不依赖模型。
    """

    __test__ = False  # 标记为非测试类 (pytest -k "not test" 零干扰)

    def __init__(self, work_dir: str | None = None) -> None:
        self._work_dir = Path(work_dir) if work_dir else Path.cwd()
        self._key_dir = self._work_dir / ".zall"
        self._key_dir.mkdir(parents=True, exist_ok=True)
        self._key_path = self._key_dir / "trust_anchor_key"
        self._log_path = self._key_dir / "trust_anchor.log"
        self._init_path = self._key_dir / "trust_anchor_init.txt"
        # O1: memorycache最后 hash, 避免每次 write_run_tail 都读盘
        self._cached_last_hash: str | None = None
        # O(n) 修复: 行数缓存, 避免每次追加都全量读盘数行数再决定是否 trim
        self._line_count: int | None = None

        # load或生成 ed25519 key
        self._private_key = self._load_or_create_key()
        self._public_key = self._private_key.public_key()
        self._anchor_id = _compute_key_fingerprint(self._public_key)[:16]

        # out-of-band init化指纹 (仅首次生成)
        self._maybe_write_init()

    # ── property (满足 TrustAnchor Protocol) ──

    @property
    def anchor_id(self) -> str:
        return self._anchor_id

    @property
    def public_key_fp(self) -> str:
        """公钥 SHA-256 指纹 (供 out-of-band validate)。"""
        return _compute_key_fingerprint(self._public_key)

    # ── sign (TrustAnchor Protocol) ──

    def write_run_tail(
        self, run_id: str, last_event_hash: str, ts: int
    ) -> AckEvent:
        """对链尾 hash sign, 追加到anchorlog, return AckEvent。

        §6.5.2.4: 签名内容 = last_event_hash || ts || run_id
        §6.5.2.5: ack 自身链式 (prev_anchor_hash)
        """
        # 1. 计算sign
        msg = _build_sign_message(last_event_hash, ts, run_id)
        sig = self._private_key.sign(msg)
        sig_hex = sig.hex()

        # 2. anchor自身chain hash
        prev_anchor_hash = self._read_last_anchor_hash()

        # 3. 追加anchorlog
        ack = AckEvent(
            anchor_id=self._anchor_id,
            run_id=run_id,
            last_event_hash=last_event_hash,
            ts=ts,
            sig=sig_hex,
            prev_anchor_hash=prev_anchor_hash,
        )
        self._append_log(ack)
        # O1: 新write后cache失效
        self._cached_last_hash = None
        return ack

    def verify(self, ack: AckEvent) -> bool:
        """validate AckEvent 的 ed25519 sign。

        纯函数: 不修改状态。
        S2 fix: 使用 _verify_sign_message 确保 sign/verify 格式一致。
        """
        sig_bytes = bytes.fromhex(ack.sig)
        return _verify_sign_message(
            self._public_key, sig_bytes,
            ack.last_event_hash, ack.ts, ack.run_id,
        )

    def verify_log_chain(self) -> bool:
        """validateanchorlog的链完整性。

        逐条检查 prev_anchor_hash 匹配 + 签名验证。
        Counterexample: 篡改某条 ack → 签名失败或哈希不匹配。

        首条宽容语义: 日志超过 _ANCHOR_LOG_MAX_ENTRIES 时 _trim_log_if_needed
        会淘汰最旧 1/3, 新首条 prev_anchor_hash 继承被淘汰段的末条 hash —
        首条不是 genesis 而是"裁剪缝合点"是设计行为, 并非篡改信号。
        可验证的剩余强度: 每条签名合法 (ed25519 不可伪造) + 首条之后
        逐条 prev 连续 (防插入/删除/重排)。首条的 prev 指向已删除的段,
        物理上不可验证 — 这是淘汰的固有代价, 不因首条特殊而误判损坏。
        """
        prev: str | None = None
        for line in self._read_log_lines():
            try:
                entry = json.loads(line)
                ack = AckEvent(**entry)
            except Exception:
                return False  # 解析失败 = 日志被篡改
            if prev is not None and ack.prev_anchor_hash != prev:
                return False
            if not self.verify(ack):
                return False
            prev = _compute_ack_hash(ack)
        return True

    # ── 内部 ──

    def _load_or_create_key(self) -> Ed25519PrivateKey:
        """load或生成 ed25519 私钥。"""
        if self._key_path.exists():
            try:
                pem = self._key_path.read_bytes()
                return Ed25519PrivateKey.from_private_bytes(pem)
            except Exception:
                # M5: silent regeneration is a security concern — re-raise
                raise
        key = Ed25519PrivateKey.generate()
        self._key_path.write_bytes(
            key.private_bytes_raw()
        )
        # 尽量设只读authority (非 root 下可篡改, 但至少防 agent process误写)
        _set_restricted_perms(self._key_path)
        return key

    def _maybe_write_init(self) -> None:
        """首次init化时write out-of-band 指纹file。

        §6.5.2.3: user 通过对比此指纹验证锚点未被 silent 替换。
        """
        if self._init_path.exists():
            return
        init_data = TrustAnchorInit(
            anchor_id=self._anchor_id,
            public_key_fp=self.public_key_fp,
            ts_init=int(time.time() * 1000),
            anchor_software_version="0.0.10",
        )
        self._init_path.write_text(
            json.dumps(init_data.model_dump(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        _set_restricted_perms(self._init_path)

    def _append_log(self, ack: AckEvent) -> None:
        """追加一条 AckEvent 到anchorlog (JSONL)。

        当日志超过 _ANCHOR_LOG_MAX_ENTRIES 条时, 淘汰最旧的 1/3 条目,
        防止日志无界增长拖慢 _read_last_anchor_hash 的读盘解析。
        """
        line = json.dumps(ack.model_dump(), ensure_ascii=False)
        with open(self._log_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
            f.flush()
            os.fsync(f.fileno())
        _set_restricted_perms(self._log_path)

        # 行数缓存增量维护; None 时惰性全量读一次建立基线
        if self._line_count is not None:
            self._line_count += 1
        else:
            self._line_count = len(self._read_log_lines())

        # 淘汰最旧的 1/3 条目 (仅当超出上限)
        self._trim_log_if_needed()

    def _trim_log_if_needed(self) -> None:
        """当日志条数超过 _ANCHOR_LOG_MAX_ENTRIES 时, 淘汰最旧的 1/3 条目。

        保留链尾的 prev_anchor_hash 不中断: 重写文件时首条 prev_anchor_hash
        继承被淘汰段的最后一条 hash, 保持链完整性。
        """
        try:
            if self._line_count is None:
                self._line_count = len(self._read_log_lines())
            if self._line_count <= _ANCHOR_LOG_MAX_ENTRIES:
                return
            # 淘汰最旧 1/3: 需要全量行做原子重写
            lines = self._read_log_lines()
            if len(lines) <= _ANCHOR_LOG_MAX_ENTRIES:
                self._line_count = len(lines)
                return
            # 保留最新的 2/3, 计算有多少条需要淘汰
            keep_count = max(_ANCHOR_LOG_MAX_ENTRIES * 2 // 3, 1)
            trimmed = lines[-keep_count:]
            # 新首条 = 原保留段首条; 其 prev_anchor_hash 继承被淘汰段末条 hash
            last_kept = json.loads(trimmed[0])
            # 找被淘汰段的最后一条, 取其 hash 作为新首条的 prev
            dropped = lines[:-keep_count]
            if dropped:
                try:
                    last_dropped = json.loads(dropped[-1])
                    last_dropped_hash = _compute_ack_hash(AckEvent(**last_dropped))
                    trimmed[0] = json.dumps({**last_kept, "prev_anchor_hash": last_dropped_hash}, ensure_ascii=False)
                except Exception:
                    pass  # 继承失败时保留原 prev_anchor_hash (链尾仍可验证)
            # 原子重写
            tmp = self._log_path.with_suffix(".log.tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                f.write("\n".join(trimmed) + "\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(str(tmp), str(self._log_path))
            # 缓存失效, 下次读盘重新解析
            self._cached_last_hash = None
            self._line_count = keep_count
        except Exception:
            # 淘汰失败不阻塞主线 (IPR-0); 行数缓存置 None 下次重新校准
            self._line_count = None

    def _read_log_lines(self) -> list[str]:
        """readanchorlog (return行list)。"""
        if not self._log_path.exists():
            return []
        try:
            return [
                line.strip()
                for line in self._log_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        except (OSError, UnicodeDecodeError):
            return []

    def _read_last_anchor_hash(self) -> str:
        """read上一条 ack 的 hash (anchor自身链式)。O1: 用memorycache。"""
        if self._cached_last_hash is not None:
            return self._cached_last_hash
        lines = self._read_log_lines()
        if not lines:
            self._cached_last_hash = "0" * 64
            return self._cached_last_hash
        # M4: walk backwards to find the last valid line instead of returning
        # genesis on parse failure (which silently corrupts the chain).
        for i in range(len(lines) - 1, -1, -1):
            try:
                last = json.loads(lines[i])
                self._cached_last_hash = _compute_ack_hash(AckEvent(**last))
                return self._cached_last_hash
            except Exception:
                continue  # skip corrupted lines, keep looking backwards
        self._cached_last_hash = "0" * 64
        return self._cached_last_hash  # no valid line found at all


# ──────────────────────────────────────────────────────────────────────────
# 辅助function (纯密码学, 守 IPR-3)
# ──────────────────────────────────────────────────────────────────────────


def _build_sign_message(last_event_hash: str, ts: int, run_id: str) -> bytes:
    """constructsignmessage: last_event_hash || ts || run_id (§6.5.2.4)。

    S2 fix: 格式必须与 FileTrustAnchor.verify() 保持一致。
    变更此函数时, 必须同步更新 verify() 中对应的签名验证格式。
    添加自检测试: 确保 sign 和 verify 使用相同格式。
    """
    return f"{last_event_hash}|{ts}|{run_id}".encode()


def _verify_sign_message(
    public_key: Ed25519PublicKey, sig: bytes, last_event_hash: str, ts: int, run_id: str
) -> bool:
    """validatesignmessage (与 _build_sign_message 格式严格一致)。

    S2 fix: 提取为独立函数, 确保 sign 和 verify 使用同一格式。
    变更 _build_sign_message 时, 必须同步更新此函数。
    """
    msg = _build_sign_message(last_event_hash, ts, run_id)
    try:
        public_key.verify(sig, msg)
        return True
    except (InvalidSignature, ValueError):
        return False


def _compute_key_fingerprint(pubkey: Ed25519PublicKey) -> str:
    """计算公钥 SHA-256 指纹 (hex)。"""
    raw = pubkey.public_bytes_raw()
    return hashlib.sha256(raw).hexdigest()


def _compute_ack_hash(ack: AckEvent) -> str:
    """计算 AckEvent 的 SHA-256 hash (anchor自身链式)。"""
    data = json.dumps(
        {
            "anchor_id": ack.anchor_id,
            "run_id": ack.run_id,
            "last_event_hash": ack.last_event_hash,
            "ts": ack.ts,
            "sig": ack.sig,
            "prev_anchor_hash": ack.prev_anchor_hash,
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _set_restricted_perms(filepath: Path) -> None:
    """尽量settingfile为仅 owner 读写 (os.chmod, 非 root security)。"""
    try:
        # file: rw------- (0o600)
        filepath.chmod(0o600)
        # directory: rwx------ (0o700)
        parent = filepath.parent
        parent.chmod(0o700)
    except OSError:
        pass  # Windows ACL 不完全等价, 静默失败 (承诺边界 §6.5.2.8)
