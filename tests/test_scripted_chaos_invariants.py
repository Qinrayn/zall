"""G15 scripted/chaos adapter 不变量测试 (IPR-0, 含反例).

不变量:
  I-SC-1  回放顺序保真; 耗尽 → STOP 收尾 (反例: loop=True 从头循环)
  I-SC-2  tool_calls 解析 + stop_reason 推断 TOOL_USE (反例: 无 calls → STOP)
  I-SC-3  流式语义 ≡ 阻塞: delta 拼接 == content, 收尾帧等价
  I-SC-4  from_file 往返 + 坏脚本报错 (反例)
  I-CH-1  probability=0 全透传 (反例: 不乱注入)
  I-CH-2  前进性护栏: probability=1 也在 max_consecutive 后放行
  I-CH-3  注入形态与真实错误路径同构 (RETRYABLE_EXC / is_retryable_status)
  I-CH-4  委托完整性: model_name 包装 / 未知属性透传 / inner 无流式则 hasattr False
  I-BLD-1 _build_adapter 接线: scripted: 前缀 / ZALL_CHAOS 包装 (反例: 无 env 不包装)
"""

from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

from zall.adapters.chaos import ChaosAdapter
from zall.adapters.scripted import ScriptedAdapter
from zall.core.model import Message, ModelResponse, StopReason, ToolChoice


def _msgs() -> list[Message]:
    return [Message.user("hi")]


# ── I-SC-1 回放顺序 ──


def test_replay_order_and_exhaustion():
    a = ScriptedAdapter([{"content": "one"}, {"content": "two"}])
    assert a.complete(_msgs(), []).content == "one"
    assert a.complete(_msgs(), []).content == "two"
    third = a.complete(_msgs(), [])
    assert third.stop_reason == StopReason.STOP
    assert third.raw.get("exhausted") is True
    assert a.calls == [(1, 0)] * 3  # 调用记录


def test_loop_mode_counterexample():
    """反例: loop=True 时耗尽后从头循环, 不进 exhausted。"""
    a = ScriptedAdapter([{"content": "x"}], loop=True)
    for _ in range(3):
        assert a.complete(_msgs(), []).content == "x"


# ── I-SC-2 tool_calls ──


def test_tool_calls_infer_tool_use():
    a = ScriptedAdapter([
        {"tool_calls": [{"id": "t1", "tool_id": "read_file", "args": {"path": "a.py"}}]},
    ])
    r = a.complete(_msgs(), [])
    assert r.stop_reason == StopReason.TOOL_USE
    assert r.tool_calls[0].tool_id == "read_file"
    assert r.tool_calls[0].args == {"path": "a.py"}


def test_no_calls_stop_counterexample():
    """反例: 无 tool_calls 时不得推断 TOOL_USE。"""
    r = ScriptedAdapter([{"content": "plain"}]).complete(_msgs(), [])
    assert r.stop_reason == StopReason.STOP and not r.tool_calls


# ── I-SC-3 流式 ≡ 阻塞 ──


def test_stream_semantics_equal_blocking():
    text = "希腊美学: " + "abcdefgh" * 8  # 跨多块 + CJK
    a = ScriptedAdapter([{"content": text, "usage": {"prompt": 3}}])
    frames = list(a.complete_stream(_msgs(), []))
    deltas = "".join(d for d, _ in frames)
    assert deltas == text
    final = frames[-1][1]
    assert final.content == text
    assert final.usage == {"prompt": 3}


# ── I-SC-4 from_file ──


def test_from_file_roundtrip(tmp_path: Path):
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"responses": [{"content": "回放"}]}, ensure_ascii=False),
                 encoding="utf-8")
    a = ScriptedAdapter.from_file(p)
    assert a.complete(_msgs(), []).content == "回放"
    assert "s.json" in a.model_name


def test_bad_script_raises_counterexample(tmp_path: Path):
    """反例: 缺 responses / 空列表 → 构造期立即报错, 不留到回放中途。"""
    p = tmp_path / "bad.json"
    p.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError):
        ScriptedAdapter.from_file(p)
    with pytest.raises(ValueError):
        ScriptedAdapter([])


# ── I-CH-1/2 chaos 概率与护栏 ──


def test_chaos_zero_probability_passthrough_counterexample():
    """反例: probability=0 绝不注入。"""
    inner = ScriptedAdapter([{"content": "ok"}] * 5, loop=True)
    c = ChaosAdapter(inner, probability=0.0, rng=random.Random(1))
    for _ in range(20):
        assert c.complete(_msgs(), []).content == "ok"
    assert c.stats["injected"] == 0


def test_chaos_progress_guarantee():
    """I-CH-2: probability=1 + max_consecutive=2 → 每 3 次必有 1 次真响应。"""
    inner = ScriptedAdapter([{"content": "real"}], loop=True)
    c = ChaosAdapter(inner, probability=1.0, modes=("429",),
                     max_consecutive=2, rng=random.Random(7))
    got_real = 0
    for _ in range(9):
        r = c.complete(_msgs(), [])
        if r.content == "real":
            got_real += 1
    assert got_real == 3  # 9 次调用 = 3 组 (注入,注入,放行)
    assert c.stats == {"injected": 6, "passed": 3}


# ── I-CH-3 注入形态同构 ──


def test_chaos_api_mode_matches_retry_path():
    from zall.adapters.base import RetryBudget

    inner = ScriptedAdapter([{"content": "r"}], loop=True)
    c = ChaosAdapter(inner, probability=1.0, modes=("429",),
                     max_consecutive=1, rng=random.Random(3))
    r = c.complete(_msgs(), [])
    assert isinstance(r, ModelResponse)
    status = r.raw["status"]
    assert RetryBudget.is_retryable_status(status)  # 走 API 重试路径


def test_chaos_transport_mode_is_retryable_exc():
    import httpx

    from zall.adapters.base import BaseAdapter

    inner = ScriptedAdapter([{"content": "r"}], loop=True)
    c = ChaosAdapter(inner, probability=1.0, modes=("transport",),
                     max_consecutive=1, rng=random.Random(3))
    with pytest.raises(BaseAdapter.RETRYABLE_EXC) as ei:
        c.complete(_msgs(), [])
    assert isinstance(ei.value, httpx.ConnectError)


def test_chaos_invalid_config_counterexample():
    """反例: 非法概率 / 未知模式 → 构造期报错。"""
    inner = ScriptedAdapter([{"content": "r"}])
    with pytest.raises(ValueError):
        ChaosAdapter(inner, probability=1.5)
    with pytest.raises(ValueError):
        ChaosAdapter(inner, modes=("teapot",))


# ── I-CH-4 委托完整性 ──


def test_chaos_delegation_and_stream_probe():
    inner = ScriptedAdapter([{"content": "s"}], loop=True)
    c = ChaosAdapter(inner, probability=0.0)
    assert c.model_name == "chaos(scripted)"
    assert hasattr(c, "complete_stream")  # inner 有 → 有
    deltas = "".join(d for d, _ in c.complete_stream(_msgs(), []))
    assert deltas == "s"

    class _NoStream:
        model_name = "bare"

        def complete(self, *a, **k):  # pragma: no cover
            raise NotImplementedError

    c2 = ChaosAdapter(_NoStream(), probability=0.0)
    # 反例: inner 无流式 → 包装层不得假装有 (loop hasattr 探测降级)
    assert not hasattr(c2, "complete_stream")


# ── I-BLD-1 _build_adapter 接线 ──


def test_build_adapter_scripted_prefix(tmp_path: Path):
    from zall.cli.config import _build_adapter

    p = tmp_path / "e2e.json"
    p.write_text(json.dumps({"responses": [{"content": "wired"}]}), encoding="utf-8")
    a = _build_adapter("openai", model=f"scripted:{p}")
    assert isinstance(a, ScriptedAdapter)
    assert a.complete(_msgs(), [], ToolChoice.AUTO).content == "wired"


def test_build_adapter_chaos_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from zall.cli.config import _build_adapter

    p = tmp_path / "e2e.json"
    p.write_text(json.dumps({"responses": [{"content": "w"}]}), encoding="utf-8")
    # 反例: 无 ZALL_CHAOS → 不包装
    monkeypatch.delenv("ZALL_CHAOS", raising=False)
    monkeypatch.setenv("ZALL_SCRIPT", str(p))
    a = _build_adapter("openai")
    assert isinstance(a, ScriptedAdapter)
    # scripted 路径优先于 chaos (回放已是确定性设施, 不叠加)
    monkeypatch.setenv("ZALL_CHAOS", "0.5")
    a2 = _build_adapter("openai")
    assert isinstance(a2, ScriptedAdapter)
    # 真实构建路径: 去掉 script → chaos 包装生效
    monkeypatch.delenv("ZALL_SCRIPT")
    monkeypatch.setenv("ZALL_CHAOS_MODES", "429,500")
    a3 = _build_adapter("openai", model="gpt-4o-mini")
    assert isinstance(a3, ChaosAdapter)
    assert a3.model_name.startswith("chaos(")
