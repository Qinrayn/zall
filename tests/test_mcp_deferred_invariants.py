"""MCP 延迟后台加载 (DeferredMCPLoader, kimi 三段式状态机对标) 不变量测试.

IPR-0: each test must contain a counterexample.

Protected invariants:
  I-MCPDEF-1: 三段式状态机 idle → loading → ready; start() 幂等 (二次 False)。
  I-MCPDEF-2: wait() — 未启动返回空 (不阻塞); 已启动收敛返回工具; 超时返回空。
  I-MCPDEF-3: 后台加载抛异常 → 降级空工具集 + error 记录, 不向调用方传播 (IPR-0)。
  I-MCPDEF-4: 加载日志进 status()["log"], 不直写 stderr (TUI 不被污染)。
"""

from __future__ import annotations

import threading
import time

import pytest

from zall.mcp.deferred import DeferredMCPLoader


def test_state_machine_and_idempotent_start() -> None:
    gate = threading.Event()

    def slow_load():
        gate.wait(timeout=5)
        return ["tool_a"]

    loader = DeferredMCPLoader(load_fn=slow_load)
    assert loader.state == "idle"
    assert loader.start() is True
    assert loader.state == "loading"
    assert loader.start() is False          # 反例: 二次启动被拒
    gate.set()
    tools = loader.wait(timeout=5)
    assert tools == ["tool_a"]
    assert loader.state == "ready"
    assert loader.status()["tool_count"] == 1


def test_wait_without_start_returns_empty_immediately() -> None:
    """反例: 未启动 wait() 不得阻塞、不得报错。"""
    loader = DeferredMCPLoader(load_fn=lambda: ["x"])
    t0 = time.monotonic()
    assert loader.wait(timeout=10) == []
    assert time.monotonic() - t0 < 1.0      # 立即返回


def test_wait_timeout_returns_empty() -> None:
    gate = threading.Event()
    loader = DeferredMCPLoader(load_fn=lambda: gate.wait(5) or [])
    loader.start()
    assert loader.wait(timeout=0.05) == []  # 超时不阻塞主流程
    gate.set()


def test_load_failure_degrades_to_empty(caplog: pytest.LogCaptureFixture) -> None:
    """IPR-0: 后台异常不传播, 降级空工具集 + error 可见。"""
    def boom():
        raise RuntimeError("server exploded")

    loader = DeferredMCPLoader(load_fn=boom)
    loader.start()
    assert loader.wait(timeout=5) == []     # 不抛
    st = loader.status()
    assert st["state"] == "ready"
    assert "server exploded" in st["error"]


def test_wait_returns_copy() -> None:
    """反例: wait() 返回副本, 调用方修改不得污染 loader 内部。"""
    loader = DeferredMCPLoader(load_fn=lambda: ["a", "b"])
    loader.start()
    got = loader.wait(timeout=5)
    got.append("c")
    assert loader.wait(timeout=5) == ["a", "b"]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
