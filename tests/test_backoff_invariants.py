"""G13 指数抖动退避不变量测试 (IPR-0, 含反例).

不变量:
  I-BO-1  jitter=0 退化为确定性指数序列 2/4/8 (非旧线性 2/4/6, 防悄悄回退)
  I-BO-2  maximum 封顶; 放宽 maximum 则继续翻倍 (反例)
  I-BO-3  抖动区间 [base*(1-j/2), base*(1+j/2)), rng 可注入确定验证
  I-BO-4  rng=0.5 时期望值恰为 base (居中对称)
  I-BO-5  attempt < 1 防御按 1 处理
  I-BO-6  架构: 三消费点无残留 `attempt * 2` 硬编码
"""

from __future__ import annotations

from pathlib import Path

from zall._util.backoff import backoff_delay

# ── I-BO-1 指数序列 ──


def test_deterministic_exponential_sequence():
    assert [backoff_delay(a, jitter=0) for a in (1, 2, 3)] == [2.0, 4.0, 8.0]


def test_not_old_linear_counterexample():
    """反例守卫: attempt 3 是 8 (指数) 而非 6 (旧线性), 防悄悄回退。"""
    assert backoff_delay(3, jitter=0) != 6.0


# ── I-BO-2 封顶 ──


def test_maximum_caps_growth():
    assert backoff_delay(10, jitter=0) == 8.0


def test_relaxed_maximum_keeps_doubling_counterexample():
    """反例: 放宽 maximum 后 attempt 4 → 16 (证明封顶来自 maximum)。"""
    assert backoff_delay(4, jitter=0, maximum=100.0) == 16.0


# ── I-BO-3 抖动区间 ──


def test_jitter_bounds_with_injected_rng():
    base = 4.0  # attempt=2
    lo = backoff_delay(2, rng=lambda: 0.0)
    hi = backoff_delay(2, rng=lambda: 0.999999)
    assert abs(lo - base * 0.75) < 1e-9
    assert base * 0.75 <= lo < hi < base * 1.25


def test_jitter_samples_within_band():
    import random

    r = random.Random(42)
    for _ in range(200):
        d = backoff_delay(3, rng=r.random)  # base=8
        assert 6.0 <= d < 10.0


# ── I-BO-4 居中 ──


def test_midpoint_rng_yields_exact_base():
    assert abs(backoff_delay(1, rng=lambda: 0.5) - 2.0) < 1e-9
    assert abs(backoff_delay(3, rng=lambda: 0.5) - 8.0) < 1e-9


# ── I-BO-5 防御 ──


def test_attempt_below_one_clamped():
    assert backoff_delay(0, jitter=0) == 2.0
    assert backoff_delay(-5, jitter=0) == 2.0


# ── I-BO-6 消费点无残留硬编码 ──


def test_no_hardcoded_linear_backoff_left():
    src = Path(__file__).resolve().parents[1] / "src" / "zall"
    for rel in ("core/loop.py", "cli/repl_ui.py", "cli/tui/app.py"):
        text = (src / rel).read_text(encoding="utf-8")
        assert "attempt * 2" not in text, f"{rel} 仍有旧线性退避"
        assert "backoff_delay" in text, f"{rel} 未接线 backoff_delay"
