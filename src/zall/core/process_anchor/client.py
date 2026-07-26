"""zall.core.process_anchor.client — ProcessTrustAnchor client (DESIGN.md §6.5.2 Phase 1).

ProcessTrustAnchor 是 TrustAnchor Protocol 的客户端实现。
它不作为 agent 进程的一部分持有私钥, 而是通过 IPC 连接到独立的 anchor server。

承诺边界 (§6.5.2.2):
  ✅ agent 进程篡改可发现 (私钥在独立进程, agent 进程内无密钥)
  ✅ 同 OS user 其他非 root 进程篡改可发现
  ❌ 同 OS user 本人 / OS root 篡改 (须远程/硬件 token)

IPR constraints:
  IPR-0: invariant tests at tests/test_process_anchor_invariants.py
         test_anchor_is_external_to_agent_process (反例: 同一进程 → fail)
  IPR-3: stdlib + cryptography only, no model SDK
"""

from __future__ import annotations

import os
import socket
from pathlib import Path
from typing import Any

from zall.core.verifiability import AckEvent, TrustAnchor

from .protocol import (
    DEFAULT_SOCKET_PATH,
    NONCE_BYTES,
    AnchorRequest,
    AnchorResponse,
    decode_message,
    encode_message,
)


def _nonce() -> str:
    """生成防重放 nonce。"""
    return os.urandom(NONCE_BYTES).hex()


class ProcessTrustAnchor(TrustAnchor):
    """ProcessTrustAnchor — 通过 IPC 连接独立 anchor server 的 TrustAnchor 实现。

    用法:
      anchor = ProcessTrustAnchor(socket_path="/tmp/zall_anchor.sock")
      ack = anchor.write_run_tail(run_id, last_event_hash, ts)

    注意:
      - 必须先启动 anchor server (`zall anchor start` 或手动启动 process_anchor.server)
      - 连接失败时返回 None (不假装签名), 调用方 fallback 到 FileTrustAnchor 或 undecidable
      - public_key_fp 属性可在连接后获取, 用于 out-of-band 验证

    传输选择:
      - 默认 Unix 用 Unix domain socket (AF_UNIX)
      - 默认 Windows 用 TCP loopback (127.0.0.1:19881) (named pipe 需 pywin32)
      - 可显式指定 transport="unix" 或 "tcp" 覆盖默认
    """

    __test__ = False

    def __init__(
        self,
        *,
        socket_path: str | None = None,
        host: str | None = None,
        port: int | None = None,
        transport: str | None = None,  # "unix" | "tcp" | None (auto)
        timeout: float = 5.0,
        work_dir: str | None = None,
    ) -> None:
        """构造 ProcessTrustAnchor。

        Args:
            socket_path: Unix domain socket 路径 (Linux/macOS, transport=unix 时用)
            host: TCP host (transport=tcp 时用, 默认 127.0.0.1)
            port: TCP port (transport=tcp 时用, 默认 19881)
            transport: 传输方式 ("unix" / "tcp", None=自动根据 OS 选择)
            timeout: IPC 调用超时 (秒)
            work_dir: 工作目录 (Windows 用; Unix 忽略)
        """
        self._socket_path = socket_path or DEFAULT_SOCKET_PATH
        self._host = host or "127.0.0.1"
        self._port = port or 19881
        self._transport = transport  # None = auto
        self._timeout = timeout
        self._work_dir = Path(work_dir) if work_dir else Path.cwd()

        # 缓存: anchor_id + public_key_fp (首次连接时获取)
        self._cached_anchor_id: str | None = None
        self._cached_public_key_fp: str | None = None

    def _effective_transport(self) -> str:
        """决定实际使用的传输方式。"""
        if self._transport is not None:
            return self._transport
        return "unix" if os.name != "nt" else "tcp"

    # ── property (满足 TrustAnchor Protocol) ──

    @property
    def anchor_id(self) -> str:
        """anchor 标识符 (首次连接时获取, 之后缓存)。"""
        if self._cached_anchor_id is None:
            self._ensure_connected()
        if self._cached_anchor_id is None:
            raise RuntimeError("cannot determine anchor_id — anchor server unreachable")
        return self._cached_anchor_id

    @property
    def public_key_fp(self) -> str:
        """anchor server 的公钥指纹 (out-of-band 验证用)。"""
        if self._cached_public_key_fp is None:
            self._ensure_connected()
        if self._cached_public_key_fp is None:
            raise RuntimeError("cannot determine public_key_fp — anchor server unreachable")
        return self._cached_public_key_fp

    # ── sign (TrustAnchor Protocol) ──

    def write_run_tail(
        self, run_id: str, last_event_hash: str, ts: int
    ) -> AckEvent | None:
        """向 anchor server 发送签名请求, 返回 AckEvent。

        若 anchor server 不可达, 返回 None (诚实退让, 不假装签名)。
        调用方 (RunRecorder.anchor_to) 应处理 None 情况。
        """
        nonce = _nonce()
        req = AnchorRequest.build(
            run_id=run_id,
            last_event_hash=last_event_hash,
            ts=ts,
            nonce=nonce,
        )

        try:
            resp = self._send(req)
        except Exception:
            # 诚实退让: 不可达 → 不签名
            return None

        if resp.error:
            return None

        payload = resp.to_ack_event_payload()
        if payload is None:
            return None

        # 构造 AckEvent (与 FileTrustAnchor 保持一致的结构)
        ack = AckEvent(
            anchor_id=payload["anchor_id"],
            run_id=payload["run_id"],
            last_event_hash=payload["last_event_hash"],
            ts=payload.get("ts", ts),
            sig=payload["sig"],
            prev_anchor_hash=payload["prev_anchor_hash"],
        )
        return ack

    # ── 内部 IPC ──

    def _ensure_connected(self) -> None:
        """建立连接并获取 anchor 元信息 (anchor_id + public_key_fp)。"""
        req = AnchorRequest(
            method="ping",
            run_id="",
            last_event_hash="",
            ts=0,
            nonce=_nonce(),
        )
        try:
            resp = self._send(req)
        except Exception as e:
            raise ConnectionError(f"anchor server unreachable: {e}") from e

        if resp.error:
            raise ConnectionError(f"anchor server error: {resp.error}")

        self._cached_anchor_id = resp.anchor_id or "unknown"
        # public_key_fp 可通过扩展协议获取; 当前版本暂未暴露, 用 anchor_id 代理
        self._cached_public_key_fp = self._cached_anchor_id[:32]  # 临时代理

    def _send(self, req: AnchorRequest) -> AnchorResponse:
        """发送请求并等待响应 (根据 transport 选择)。"""
        transport = self._effective_transport()
        if transport == "unix":
            return self._send_unix(req)
        return self._send_tcp(req)

    def _send_unix(self, req: AnchorRequest) -> AnchorResponse:
        """Unix domain socket 发送。"""
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self._timeout)
        try:
            sock.connect(self._socket_path)
            sock.sendall(encode_message(req))
            data = self._recv_line(sock)
            resp = AnchorResponse(**decode_message(data))
        finally:
            sock.close()
        return resp

    def _send_tcp(self, req: AnchorRequest) -> AnchorResponse:
        """TCP 发送 (Windows 回退 + 显式 tcp 模式)。"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(self._timeout)
        try:
            sock.connect((self._host, self._port))
            sock.sendall(encode_message(req))
            data = self._recv_line(sock)
            resp = AnchorResponse(**decode_message(data))
        finally:
            sock.close()
        return resp

    def _recv_line(self, sock: socket.socket) -> bytes:
        """从 socket 读取一行 (换行分隔)。"""
        data = b""
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            data += chunk
            if b"\n" in data:
                break
        return data


# ── 辅助: 启动 anchor server 的便捷函数 ──


def start_anchor_server(
    *,
    socket_path: str | None = None,
    host: str | None = None,
    port: int | None = None,
    work_dir: str | None = None,
    background: bool = True,
) -> Any:
    """启动独立的 anchor server 进程 (subprocess)。

    返回 subprocess.Popen 对象。调用方可在适当时终止它。

    注意:
      - 这是开发/测试便捷函数; 生产环境应由 systemd / service manager 管理
      - anchor server 持有私钥, 不应随意终止
    """
    import subprocess

    cmd = [
        "python",
        "-m",
        "zall.core.process_anchor.server",
    ]
    if socket_path:
        cmd += ["--socket-path", socket_path]
    if host:
        cmd += ["--host", host]
    if port is not None:
        cmd += ["--port", str(port)]
    if work_dir:
        cmd += ["--work-dir", work_dir]

    kwargs: dict[str, Any] = {"stdout": subprocess.PIPE, "stderr": subprocess.PIPE}
    if background:
        kwargs["start_new_session"] = True

    return subprocess.Popen(cmd, **kwargs)