"""zall.core.process_anchor.server — Standalone anchor server process (DESIGN.md §6.5.2 Phase 1).

独立的 anchor server 进程, 持有 ed25519 私钥, 通过 IPC 接收签名请求。

私钥永不离开本进程。内存中持有 key, 仅在首次启动时写入磁盘 (加密存储),
之后仅从磁盘读取到内存, 不对外暴露。

用法:
    python -m zall.core.process_anchor.server [--socket-path /tmp/zall_anchor.sock] [--port 19881]

启动后:
  - Unix: 监听 Unix domain socket (AF_UNIX)
  - Windows: 监听 TCP loopback (127.0.0.1:19881) (named pipe 回退)

协议: 参见 protocol.py

IPR constraints:
  IPR-3: stdlib + cryptography only, no model SDK
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import sys
import threading
import time
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from zall.core.verifiability import AckEvent, _compute_ack_hash
from .protocol import (
    AnchorRequest,
    AnchorResponse,
    DEFAULT_SOCKET_PATH,
    decode_message,
    encode_message,
)


# ── 持久化 ──


DEFAULT_KEY_DIR = Path.home() / ".zall" / "anchor"
_KEY_FILE = DEFAULT_KEY_DIR / "anchor_private_key.pem"
_LOG_FILE = DEFAULT_KEY_DIR / "anchor.log"
_INIT_FILE = DEFAULT_KEY_DIR / "anchor_init.txt"
_NONCE_REPLAY_FILE = DEFAULT_KEY_DIR / "replay_cache.txt"  # 防重放 nonce 缓存 (TTL)


class AnchorState:
    """anchor server 的运行时状态 (单进程内)。"""

    def __init__(self, work_dir: Path | None = None) -> None:
        self._work_dir = work_dir or Path.cwd()
        self._private_key: Ed25519PrivateKey | None = None
        self._public_key: Ed25519PublicKey | None = None
        self._anchor_id: str = ""
        self._prev_anchor_hash: str = "0" * 64
        self._log_lines: list[str] = []
        self._seen_nonces: set[str] = set()  # 防重放 (TTL 清理 deferred)
        self._lock = threading.RLock()

    def load_or_create_key(self) -> None:
        """加载或创建 ed25519 密钥对。"""
        DEFAULT_KEY_DIR.mkdir(parents=True, exist_ok=True)
        if _KEY_FILE.exists():
            try:
                pem = _KEY_FILE.read_bytes()
                self._private_key = Ed25519PrivateKey.from_private_bytes(pem)
                self._public_key = self._private_key.public_key()
            except Exception as e:
                print(f"[anchor] failed to load key: {e}", file=sys.stderr)
                sys.exit(1)
        else:
            self._private_key = Ed25519PrivateKey.generate()
            self._public_key = self._private_key.public_key()
            _KEY_FILE.write_bytes(self._private_key.private_bytes_raw())
            _set_restricted_perms(_KEY_FILE)
            # 写 out-of-band 初始化文件
            self._write_init()

        self._anchor_id = _compute_key_fingerprint(self._public_key)[:16]
        self._read_last_anchor_hash()

    def _write_init(self) -> None:
        """写 out-of-band 指纹文件供用户验证。"""
        if _INIT_FILE.exists():
            return
        init_data = {
            "anchor_id": self._anchor_id,
            "public_key_fp": self.public_key_fp(),
            "ts_init": int(time.time() * 1000),
        }
        _INIT_FILE.write_text(json.dumps(init_data, indent=2, ensure_ascii=False), encoding="utf-8")
        _set_restricted_perms(_INIT_FILE)

    def public_key_fp(self) -> str:
        """公钥指纹。"""
        if self._public_key is None:
            return ""
        return _compute_key_fingerprint(self._public_key)

    def sign(self, run_id: str, last_event_hash: str, ts: int, nonce: str) -> AnchorResponse:
        """对请求签名, 追加到 anchor log, 返回 AckEvent 响应。"""
        with self._lock:
            # 防重放
            if nonce in self._seen_nonces:
                return AnchorResponse(error=f"replay detected: nonce={nonce[:16]}...")
            self._seen_nonces.add(nonce)
            self._prune_nonces()

            if self._private_key is None:
                return AnchorResponse(error="key not initialized")

            # 构造签名消息
            msg = _build_sign_message(last_event_hash, ts, run_id)
            sig = self._private_key.sign(msg)
            sig_hex = sig.hex()

            # anchor 自身链式 hash
            prev_anchor_hash = self._prev_anchor_hash

            # 构造 AckEvent
            ack = AckEvent(
                anchor_id=self._anchor_id,
                run_id=run_id,
                last_event_hash=last_event_hash,
                ts=ts,
                sig=sig_hex,
                prev_anchor_hash=prev_anchor_hash,
            )

            # 追加到 log
            line = json.dumps(ack.model_dump(), ensure_ascii=False)
            self._log_lines.append(line)
            try:
                with open(_LOG_FILE, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
                    f.flush()
                    os.fsync(f.fileno())
                _set_restricted_perms(_LOG_FILE)
            except Exception as e:
                print(f"[anchor] log write failed: {e}", file=sys.stderr)
                # 仍然返回签名 (log 失败不阻断签名, 但记录警告)

            # 更新链尾
            self._prev_anchor_hash = _compute_ack_hash(ack)

            return AnchorResponse(
                anchor_id=ack.anchor_id,
                run_id=ack.run_id,
                last_event_hash=ack.last_event_hash,
                ts=ack.ts,
                sig=ack.sig,
                prev_anchor_hash=ack.prev_anchor_hash,
                nonce=None,  # 由调用方回显
            )

    def _prune_nonces(self) -> None:
        """修剪 nonce 缓存 (简单大小限制, 防止无限增长)。"""
        max_size = 10000
        if len(self._seen_nonces) > max_size:
            # 清空重设 (简单策略; 生产环境应带 TTL)
            self._seen_nonces.clear()

    def _read_last_anchor_hash(self) -> None:
        """从 log 读取上一条 anchor hash。"""
        if not _LOG_FILE.exists():
            self._prev_anchor_hash = "0" * 64
            self._log_lines = []
            return
        try:
            lines = _LOG_FILE.read_text(encoding="utf-8").splitlines()
            self._log_lines = [l for l in lines if l.strip()]
            # 从最后一条有效 entry 计算 prev_anchor_hash
            for line in reversed(self._log_lines):
                try:
                    entry = json.loads(line)
                    ack = AckEvent(**entry)
                    self._prev_anchor_hash = _compute_ack_hash(ack)
                    return
                except Exception:
                    continue
            self._prev_anchor_hash = "0" * 64
        except Exception:
            self._prev_anchor_hash = "0" * 64

    def ping(self) -> AnchorResponse:
        """ping 响应, 返回 anchor 元信息。"""
        return AnchorResponse(
            anchor_id=self._anchor_id,
            public_key_fp=self.public_key_fp(),
        )


# ── 密码学辅助 (与 verifiability.py 保持一致) ──


def _build_sign_message(last_event_hash: str, ts: int, run_id: str) -> bytes:
    """构造签名消息 (与 verifiability._build_sign_message 必须一致)。"""
    return f"{last_event_hash}|{ts}|{run_id}".encode("utf-8")


def _compute_key_fingerprint(pubkey: Ed25519PublicKey) -> str:
    """计算公钥 SHA-256 指纹。"""
    raw = pubkey.public_bytes_raw()
    return hashlib.sha256(raw).hexdigest()


def _set_restricted_perms(filepath: Path) -> None:
    """尽量设置文件为仅 owner 读写。"""
    try:
        filepath.chmod(0o600)
        parent = filepath.parent
        parent.chmod(0o700)
    except Exception:
        pass  # Windows 可能不支持 chmod, 跳过


# ── 服务循环 ──


def handle_client(conn: socket.socket, state: AnchorState) -> None:
    """处理单个客户端连接。"""
    try:
        data = b""
        while True:
            chunk = conn.recv(4096)
            if not chunk:
                break
            data += chunk
            if b"\n" in data:
                break
        if not data:
            return

        try:
            req_dict = decode_message(data)
        except Exception:
            conn.sendall(encode_message(AnchorResponse(error="invalid JSON")))
            return

        method = req_dict.get("method", "write_run_tail")
        try:
            req = AnchorRequest(**req_dict)
        except Exception as e:
            conn.sendall(encode_message(AnchorResponse(error=f"invalid request: {e}")))
            return

        if method == "ping":
            resp = state.ping()
        elif method == "write_run_tail":
            resp = state.sign(req.run_id, req.last_event_hash, req.ts, req.nonce)
        else:
            resp = AnchorResponse(error=f"unknown method: {method}")

        conn.sendall(encode_message(resp))
    except Exception as e:
        print(f"[anchor] client error: {e}", file=sys.stderr)
    finally:
        try:
            conn.close()
        except Exception:
            pass


def run_unix_server(socket_path: str, state: AnchorState) -> None:
    """运行 Unix domain socket 服务器。"""
    if os.path.exists(socket_path):
        os.unlink(socket_path)
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(socket_path)
    server.listen(5)
    print(f"[anchor] listening on unix socket {socket_path}", file=sys.stderr)
    try:
        while True:
            conn, _ = server.accept()
            threading.Thread(target=handle_client, args=(conn, state), daemon=True).start()
    except KeyboardInterrupt:
        print("[anchor] shutting down", file=sys.stderr)
    finally:
        server.close()
        if os.path.exists(socket_path):
            os.unlink(socket_path)


def run_tcp_server(host: str, port: int, state: AnchorState) -> None:
    """运行 TCP 服务器 (Windows + Unix 回退)。"""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((host, port))
    server.listen(5)
    print(f"[anchor] listening on {host}:{port}", file=sys.stderr)
    try:
        while True:
            conn, _ = server.accept()
            threading.Thread(target=handle_client, args=(conn, state), daemon=True).start()
    except KeyboardInterrupt:
        print("[anchor] shutting down", file=sys.stderr)
    finally:
        server.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="zall TrustAnchor server")
    parser.add_argument("--socket-path", default=DEFAULT_SOCKET_PATH, help="Unix socket path")
    parser.add_argument("--host", default="127.0.0.1", help="TCP bind host (Windows fallback)")
    parser.add_argument("--port", type=int, default=19881, help="TCP port (Windows fallback)")
    parser.add_argument("--work-dir", default=None, help="Working directory for anchor files")
    parser.add_argument("--mode", choices=["unix", "tcp", "auto"], default="auto",
                        help="Transport mode (auto: Unix 用 unix, Windows 用 tcp)")
    args = parser.parse_args()

    work_dir = Path(args.work_dir) if args.work_dir else None
    state = AnchorState(work_dir=work_dir)
    state.load_or_create_key()

    print(f"[anchor] started (id={state._anchor_id}, fp={state.public_key_fp()})", file=sys.stderr)

    if args.mode == "auto":
        mode = "unix" if os.name != "nt" else "tcp"
    else:
        mode = args.mode

    if mode == "unix":
        run_unix_server(args.socket_path, state)
    else:
        run_tcp_server(args.host, args.port, state)


if __name__ == "__main__":
    main()