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
import os
import time
from enum import Enum
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.exceptions import InvalidSignature
from pydantic import BaseModel, ConfigDict


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
    GOAL_DOWNGRADE = "goal_downgrade"
    PR0_HALLUCINATION = "pr0_hallucination"
    SYSTEM_INJECTION = "system_injection"  # v0.0.22: 系统注入消息 (eg. 空 STOP nudge), 守 §6.1 全保真


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

    IPR-0 不变量:
        - append 后immutable (event 是 frozen pydantic)
        - 链不断: 每条 prev_hash == 前一条 compute_hash()
        - 首条 prev_hash == "0"*64 (genesis)

    承诺边界 (§6.5.2.2):
        篡改可发现 (agent 进程级), 不防 user 本人 / OS root。
        内存版不假装 adversary-resilient —— 文件持久化后,
        通过 TrustAnchor 外部锚点 + 离线 audit 实现"篡改可发现"。
    """

    def __init__(self, run_id: str) -> None:
        self._run_id = run_id
        self._events: list[TimelineEvent] = []
        self._events_cache: tuple[TimelineEvent, ...] | None = None

    @property
    def run_id(self) -> str:
        return self._run_id

    @property
    def events(self) -> tuple[TimelineEvent, ...]:
        """所有已记录event (read-only视图, O7 cache)。"""
        if self._events_cache is None:
            self._events_cache = tuple(self._events)
        return self._events_cache

    @property
    def tail_hash(self) -> str:
        """当前链尾 hash。无event时return genesis hash。"""
        if not self._events:
            return "0" * 64
        return self._events[-1].compute_hash()

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
        return event

    def verify_chain(self) -> bool:
        """validate链完整性。

        纯函数: 不修改状态, 不调外部服务。
        逐条检查 prev_hash == 前一条 compute_hash()。
        首条 prev_hash == "0"*64 (genesis)。

        Counterexample: 如果有人篡改了某条事件的 payload, 该条 compute_hash() 变化,
        但下一条的 prev_hash 仍是旧值 → 不匹配 → verify_chain  False。
        """
        prev = "0" * 64
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
        if not self._events:
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
        """
        prev = "0" * 64
        line_num = 0
        for line in self._read_log_lines():
            line_num += 1
            try:
                entry = json.loads(line)
                ack = AckEvent(**entry)
            except Exception:
                return False  # 解析失败 = 日志被篡改
            if ack.prev_anchor_hash != prev:
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
        """追加一条 AckEvent 到anchorlog (JSONL)。"""
        line = json.dumps(ack.model_dump(), ensure_ascii=False)
        with open(self._log_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
            f.flush()
            os.fsync(f.fileno())
        _set_restricted_perms(self._log_path)

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
    return f"{last_event_hash}|{ts}|{run_id}".encode("utf-8")


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
