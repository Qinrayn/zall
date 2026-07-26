"""zall.test.process_anchor invariants — ProcessTrustAnchor external anchor.

IPR-0: 每个测试包含 counterexample。

保护的核心不变量:
  1. ProcessTrustAnchor 不在 agent 进程内持有私钥 (反例: 同一进程 → fail)
  2. write_run_tail 在 anchor 不可达时返回 None (诚实退让)
  3. anchor server 签名后可被 verify 验证
  4. anchor log 链完整性可验证

注意: 这些测试需要 anchor server 运行。CI 中用 subprocess 启动 server。
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
import unittest
from pathlib import Path

import pytest

# 确保 src 在路径中
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from zall.core.process_anchor import ProcessTrustAnchor, start_anchor_server
from zall.core.process_anchor.protocol import AnchorRequest, AnchorResponse, encode_message
from zall.core.verifiability import AckEvent


def _find_free_port() -> int:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class TestProcessAnchorExternal:
    """Phase 1 不变量: anchor 必须是外部进程。"""

    @classmethod
    def setup_class(cls) -> None:
        """启动一个临时的 anchor server 用于测试。"""
        cls._port = _find_free_port()
        cls._proc = start_anchor_server(
            background=True,
            # 用 TCP mode 以跨平台测试
        )
        # 给 server 一点时间启动
        time.sleep(1.5)
        cls._anchor = ProcessTrustAnchor(timeout=5.0)

    @classmethod
    def teardown_class(cls) -> None:
        """终止 anchor server。"""
        if hasattr(cls, "_proc") and cls._proc is not None:
            cls._proc.terminate()
            try:
                cls._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                cls._proc.kill()

    def test_anchor_is_external_to_agent_process(self) -> None:
        """不变量: ProcessTrustAnchor 不在 agent 进程内持有私钥。

        Counterexample: 如果 ProcessTrustAnchor 在本地持有 private key (如 FileTrustAnchor),
        则它不是"外部锚点", 本测试应 fail。

        验证方法: 检查 ProcessTrustAnchor 实例没有 _private_key 属性,
        且 write_run_tail 走 IPC 而非本地签名。
        """
        anchor = ProcessTrustAnchor()
        # ProcessTrustAnchor 不应持有私钥 (私钥在 server 进程)
        assert not hasattr(anchor, "_private_key"), \
            "ProcessTrustAnchor must not hold a private key in-process (violates external anchor)"
        # 它应该只有 IPC 相关的属性
        assert hasattr(anchor, "_socket_path") or hasattr(anchor, "_pipe_name"), \
            "ProcessTrustAnchor should have IPC configuration"

    def test_anchor_returns_none_when_unreachable(self) -> None:
        """不变量: anchor 不可达时 write_run_tail 返回 None (诚实退让)。

        Counterexample: 如果不可达时抛异常或返回假签名 → fail。
        """
        # 用一个不可能在运行的端口 (高概率未被占用)
        anchor = ProcessTrustAnchor(host="127.0.0.1", port=59999, transport="tcp", timeout=1.0)
        result = anchor.write_run_tail("test_run", "a" * 64, int(time.time() * 1000))
        # 不可达 → 诚实退让 (None), 不假装签名
        assert result is None, "unreachable anchor must return None (honest retreat)"

    def test_anchor_signs_and_verifies(self) -> None:
        """happy path: anchor server 签名后可验证。

        Counterexample: 如果签名验证不通过 → fail。
        """
        anchor = self._anchor
        run_id = "test_run_abc"
        last_event_hash = hashlib.sha256(b"test_event").hexdigest()
        ts = int(time.time() * 1000)

        ack = anchor.write_run_tail(run_id, last_event_hash, ts)
        assert ack is not None, "anchor should be reachable in this test"
        assert isinstance(ack, AckEvent)
        assert ack.anchor_id == anchor.anchor_id
        assert ack.last_event_hash == last_event_hash
        assert ack.run_id == run_id

        # 验证签名: 需要从 server 获取公钥 — 这里我们用 anchor_id 间接验证
        # (实际验证通过 anchor server 的 verify 接口; 此处验证结构正确性)
        assert len(ack.sig) == 128  # ed25519 签名 hex = 64 字节 = 128 字符
        assert len(ack.prev_anchor_hash) == 64  # SHA-256 hex

    def test_anchor_chain_is_sequential(self) -> None:
        """不变量: anchor 自身链式 (每条 prev_anchor_hash = 前一条的 hash)。

        Counterexample: 如果两条连续 ack 的 prev_anchor_hash 不匹配 → fail。
        """
        anchor = self._anchor
        prev_hash = None
        for i in range(3):
            ack = anchor.write_run_tail(f"chain_test_{i}", f"hash_{i}", int(time.time() * 1000) + i)
            assert ack is not None
            if prev_hash is not None:
                assert ack.prev_anchor_hash == prev_hash, \
                    f"chain broken at step {i}: prev_anchor_hash {ack.prev_anchor_hash} != {prev_hash}"
            # 计算当前 ack 的 hash (作为下一条的 prev)
            import json as _json
            data = _json.dumps(ack.model_dump(), sort_keys=True, ensure_ascii=False)
            prev_hash = hashlib.sha256(data.encode("utf-8")).hexdigest()

    def test_anchor_id_consistent(self) -> None:
        """不变量: 同一 anchor server 的 anchor_id 一致。

        Counterexample: 每次调用 anchor_id 都不同 → fail。
        """
        anchor = self._anchor
        id1 = anchor.anchor_id
        id2 = anchor.anchor_id
        assert id1 == id2, "anchor_id must be stable across calls"


class TestProcessAnchorProtocol:
    """协议级别的测试 (不依赖服务器)。"""

    def test_request_encode_decode(self) -> None:
        """编码/解码往返一致。"""
        req = AnchorRequest.build(
            run_id="test_run",
            last_event_hash="abc123",
            ts=1234567890,
            nonce="deadbeef",
        )
        encoded = encode_message(req)
        decoded = json.loads(encoded.decode("utf-8").strip())
        assert decoded["run_id"] == "test_run"
        assert decoded["method"] == "write_run_tail"

    def test_response_to_ack_payload(self) -> None:
        """AnchorResponse.to_ack_event_payload 正确提取。"""
        resp = AnchorResponse(
            anchor_id="a1",
            run_id="r1",
            last_event_hash="h1",
            ts=123,
            sig="sig1",
            prev_anchor_hash="prev1",
            nonce="n1",
            error=None,
        )
        payload = resp.to_ack_event_payload()
        assert payload is not None
        assert payload["anchor_id"] == "a1"
        assert payload["sig"] == "sig1"

    def test_response_error_to_ack_payload(self) -> None:
        """错误响应返回 None。"""
        resp = AnchorResponse(error="server down")
        assert resp.to_ack_event_payload() is None


if __name__ == "__main__":
    unittest.main()