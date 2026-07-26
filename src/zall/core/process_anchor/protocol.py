"""zall.core.process_anchor.protocol — IPC message protocol (DESIGN.md §6.5.2 Phase 1).

ProcessTrustAnchor 与 agent 进程之间的通信协议。
独立进程 (anchor server) 持有 ed25519 私钥, 通过 IPC 接收签名请求。

协议:
  请求 (agent → anchor):
    {
      "method": "write_run_tail",
      "run_id": str,
      "last_event_hash": str,   # hex 64
      "ts": int,
      "nonce": str,             # 防重放
    }

  响应 (anchor → agent):
    {
      "anchor_id": str,
      "run_id": str,
      "last_event_hash": str,
      "ts": int,
      "sig": str,               # ed25519 签名 (hex)
      "prev_anchor_hash": str,  # 锚点自身链式
      "nonce": str,             # 回显
      "error": str | None,      # 错误信息 (无错误为 null)
    }

传输:
  - Unix: Unix domain socket (AF_UNIX)
  - Windows: named pipe (Windows named pipe, prefix \\\\.\\pipe\\)

安全:
  - 私钥永不离开 anchor 进程
  - 每次请求带 nonce 防重放
  - 连接时交换 public_key_fp 供 agent out-of-band 验证

IPR constraints:
  IPR-3: stdlib + cryptography only, no model SDK
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict


# ── 请求/响应 model ──


class AnchorRequest(BaseModel):
    """agent → anchor 请求。"""

    model_config = ConfigDict(frozen=True)

    method: str = "write_run_tail"
    run_id: str
    last_event_hash: str
    ts: int
    nonce: str

    @classmethod
    def build(cls, run_id: str, last_event_hash: str, ts: int, nonce: str) -> "AnchorRequest":
        return cls(
            method="write_run_tail",
            run_id=run_id,
            last_event_hash=last_event_hash,
            ts=ts,
            nonce=nonce,
        )


class AnchorResponse(BaseModel):
    """anchor → agent 响应。"""

    model_config = ConfigDict(frozen=True)

    anchor_id: str | None = None
    public_key_fp: str | None = None
    run_id: str | None = None
    last_event_hash: str | None = None
    ts: int | None = None
    sig: str | None = None
    prev_anchor_hash: str | None = None
    nonce: str | None = None
    error: str | None = None

    def to_ack_event_payload(self) -> dict[str, str] | None:
        """若响应成功, 返回可写入 timeline 的 payload dict。"""
        if self.error or not all(
            v is not None
            for v in (self.anchor_id, self.run_id, self.last_event_hash,
                      self.ts, self.sig, self.prev_anchor_hash)
        ):
            return None
        return {
            "anchor_id": self.anchor_id,  # type: ignore[dict-item]
            "run_id": self.run_id,  # type: ignore[dict-item]
            "last_event_hash": self.last_event_hash,  # type: ignore[dict-item]
            "sig": self.sig,  # type: ignore[dict-item]
            "prev_anchor_hash": self.prev_anchor_hash,  # type: ignore[dict-item]
        }


# ── 线编码/解码 ──


def encode_message(msg: BaseModel) -> bytes:
    """将 model 编码为 JSON 行 (换行分隔, 便于流式解析)。"""
    data = msg.model_dump()
    return (json.dumps(data, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")


def decode_message(raw: bytes) -> dict[str, Any]:
    """解码一行 JSON 为 dict。"""
    return json.loads(raw.decode("utf-8").strip())


# ── 常量 ──


DEFAULT_SOCKET_PATH: str = "/tmp/zall_anchor.sock"  # Unix
DEFAULT_PIPE_NAME: str = "zall_anchor"  # Windows named pipe base
NONCE_BYTES: int = 16  # nonce 长度 (hex 32 字符)