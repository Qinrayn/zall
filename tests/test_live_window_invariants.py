"""G7 上游窗口元数据 (context_length) 不变量测试 (IPR-0).

背景 (2026-07-23 用户实测): 模型走自定义网关 (sensenova), /models 探测能拿到
每个模型的 context_length (deepseek-v4-flash = 1M, sensenova-6.8-flash-lite
= 262144), 但内置表只约写过 128k 旧值, 新模型名一律拿默认 32000 → footer
显示 "ctx 100% left / 32k", 水位压缩按 32k 提前触发。

修复: 探测时顺带抓 context_length 注入 _LIVE_WINDOWS, get_window_size 优先
真实上限; 展示层窗口未知时不伪造百分比/32k, 只显示已用 token。

不变量:
  I-LW-1  set_live_windows 后精确模型名返回真实 context_length (非默认 32K)
  I-LW-2  探测到的值覆盖内置表旧值 (sensenova-6.8 → 262144 非 32000)
  I-LW-3  window_size_known: live/内置/自定义 → True; 完全未知 → False
  I-LW-4  probe_models 顺带采集 context_length (MockTransport)
  I-LW-5  baseline 自适应: 小窗口不再永显 100% (12K 底座不再吞掉窗)
  I-LW-6  footer/status: 未知窗口只显已用 token; 已知窗口显百分比+窗口
"""

from __future__ import annotations

import httpx

from zall._util import model_registry as mr


# ── I-LW-1 / I-LW-2: 探测值覆盖默认/内置旧值 ──


def test_live_window_precise_and_overrides_default(monkeypatch) -> None:
    """路由器注入后: 精确模型名用探测到的 context_length (反例: 不再默认 32K)。"""
    monkeypatch.setattr(mr, "_LIVE_WINDOWS", {"sensenova-6.8-flash-lite": 262144})
    assert mr.get_window_size("sensenova-6.8-flash-lite") == 262144
    # 反例: 修复前这里返回默认 32000 (模型不在任何已知表里)
    assert mr.get_window_size("sensenova-6.8-flash-lite") != 32000


def test_live_window_overrides_builtin_guess(monkeypatch) -> None:
    """同模型名在网关上是 1M, 内置表只写 128k 旧值 → 探测值优先 (反例: 128k)。"""
    monkeypatch.setattr(mr, "_LIVE_WINDOWS", {"deepseek-v4-flash": 1048576})
    assert mr.get_window_size("deepseek-v4-flash") == 1048576
    assert mr.get_window_size("deepseek-v4-flash") != 128000


def test_custom_config_wins_over_live(monkeypatch) -> None:
    """用户显式配置的 window_size 是逃生阀, 优先于探测值 (A2 契约)。"""
    try:
        mr.set_custom_windows({"my-gpt": 8192})
        monkeypatch.setattr(mr, "_LIVE_WINDOWS",
                            {"my-gpt": 1048576, "free-model": 262144})
        assert mr.get_window_size("my-gpt") == 8192
        # 反例: 探测值不能反过来覆盖用户显式配置
        assert mr.get_window_size("my-gpt") != 1048576
        # 未配置的模型仍吃探测值
        assert mr.get_window_size("free-model") == 262144
    finally:
        mr.set_custom_windows({})


def test_live_window_prefix_matches_group(monkeypatch) -> None:
    """同组模型 id 是精确名超集: sensenova-6.8-x 共享该前缀的值。"""
    monkeypatch.setattr(mr, "_LIVE_WINDOWS", {"sensenova": 262144})
    assert mr.get_window_size("sensenova-6.8-flash-lite") == 262144


def test_cleanup_restores_default(monkeypatch) -> None:
    """探测失败/清空 → 回落到默认 32000 (展示层靠 window_size_known 分辨)。"""
    monkeypatch.setattr(mr, "_LIVE_WINDOWS", {})
    assert mr.get_window_size("sensenova-6.8-flash-lite") == 32000
    assert mr.window_size_known("sensenova-6.8-flash-lite") is False


# ── I-LW-3: window_size_known 语义 ──


def test_window_known_semantics(monkeypatch) -> None:
    """已知来源 (内置/自定义/live) → True; 完全未知 → False。"""
    monkeypatch.setattr(mr, "_LIVE_WINDOWS", {"sensenova-6.8-flash-lite": 262144})
    assert mr.window_size_known("gpt-4o") is True        # 内置表
    assert mr.window_size_known("deepseek-chat") is True  # 内置表
    assert mr.window_size_known("olly-unknown") is False  # 完全未知
    try:
        mr.set_custom_windows({"my-custom": 64000})
        assert mr.window_size_known("my-custom") is True
        assert mr.window_size_known("my-custom-2") is True  # 前缀
    finally:
        mr.set_custom_windows({})
    # live 注入后变为已知 — 这正是"宿主模型一探测就不猜了"的关键
    assert mr.window_size_known("sensenova-6.8-flash-lite") is True
    assert mr.window_size_known("") is False


# ── I-LW-4: probe_models 采集 context_length ──


def test_probe_models_collects_context_length() -> None:
    """/models 响应的 context_length 被采集进 windows_out (MockTransport)。"""
    from zall.cli.model_switch import probe_models

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [
            {"id": "deepseek-v4-flash", "context_length": 1048576},
            {"id": "sensenova-6.8-flash-lite", "context_length": 262144},
            {"id": "no-ctx-model"},  # 无 context_length 字段 → 不采集
        ]})

    windows: dict[str, int] = {}
    ids = probe_models("https://ex.com/v1", "sk-x",
                       transport=httpx.MockTransport(handler),
                       windows_out=windows)
    assert ids == ["deepseek-v4-flash", "no-ctx-model", "sensenova-6.8-flash-lite"]
    assert windows == {"deepseek-v4-flash": 1048576,
                       "sensenova-6.8-flash-lite": 262144}


# #### I-LW-5: baseline 自适应 (小窗口不再永久 100%/0%) ──


def test_baseline_adaptive_small_window() -> None:
    """32000 窗口: 用了 12k 还显示 100% left 是误导 → 底座自适应后真读数。"""
    from zall.core.cache_stats import context_remaining_percent as crp

    # 修复前: 底座 12000 完全吞掉 lg 84832 窗口 → 一律 0
    assert crp(0, 8192) == 100      # 不再一上来就 0%/100%
    assert crp(8000, 8192) < 100
    assert crp(8192, 8192) == 0
    # 32k 窗口: 9000 已是 1/3 → 不该再 100%
    assert crp(9000, 32000) < 100
    # 大窗口与旧语义兼容 (128k: 9000 仍 100%, 底座 12000)
    assert crp(9000, 128000) == 100
    # 未知窗口 (<=0) → None (调用方不显示)
    assert crp(9000, 0) is None
    assert crp(9000, -100) is None


def test_footer_unknown_window_shows_raw_tokens() -> None:
    """窗口未知 (fake 模型) → footer 只显真实已用 token, 不伪造百分比/32k。"""
    from zall.cli.prompt import _footer_right_segments
    from zall._util import model_registry as mr
    _old = dict(mr._LIVE_WINDOWS)
    mr._LIVE_WINDOWS.clear()
    try:
        segs = _footer_right_segments({"model": "totally-unknown-id",
                                       "ctx_tokens": 12345})
        assert any("ctx" in s for s in segs)
        ctx_seg = next(s for s in segs if s.startswith("ctx "))
        assert "%" not in ctx_seg        # 不伪造百分比
        assert "32k" not in ctx_seg      # 不伪造窗口
        assert "12.3k" in ctx_seg        # 真实已用 (format_tokens)
    finally:
        mr._LIVE_WINDOWS.update(_old)


def test_footer_known_window_shows_percent(monkeypatch) -> None:
    """窗口已知 (live 注入后) → footer 显百分比 + 真实窗口。"""
    from zall.cli.prompt import _footer_right_segments
    monkeypatch.setattr(mr, "_LIVE_WINDOWS", {"sensenova-6.8-flash-lite": 262144})
    segs = _footer_right_segments({"model": "sensenova-6.8-flash-lite",
                                   "ctx_tokens": 10000})
    ctx_seg = next(s for s in segs if s.startswith("ctx "))
    assert "100% left" in ctx_seg
    assert "/ 262k" in ctx_seg
    assert "32k" not in ctx_seg