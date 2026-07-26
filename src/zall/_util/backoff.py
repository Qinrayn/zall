"""zall._util.backoff — 指数抖动退避 (单一真相源)。

G13 (kimi wait_exponential_jitter 对标):
  此前三处消费点 (core/loop · cli/repl_ui · cli/tui/app) 各自硬编码
  `delay = attempt * 2` (2s/4s/6s) — 线性退避对慢端点体感僵硬, 且固定值
  在多客户端同时重试时产生同步惊群 (thundering herd)。

  本版: base = min(initial * 2**(attempt-1), maximum), 再乘均匀抖动
  [1 - jitter/2, 1 + jitter/2) — 期望值恰为 base (好推理), 打散重试相位。
  默认 initial=2, maximum=8, jitter=0.5 → attempt 1/2/3 ≈ [1.5,2.5)/[3,5)/[6,10)s。

IPR constraints:
  IPR-0: tests/test_backoff_invariants.py (含反例)
  IPR-3: 纯 stdlib
"""

from __future__ import annotations

import random
from typing import Callable

DEFAULT_INITIAL = 2.0
DEFAULT_MAXIMUM = 8.0
DEFAULT_JITTER = 0.5


def backoff_delay(
    attempt: int,
    *,
    initial: float = DEFAULT_INITIAL,
    maximum: float = DEFAULT_MAXIMUM,
    jitter: float = DEFAULT_JITTER,
    rng: Callable[[], float] = random.random,
) -> float:
    """第 attempt 次 (1-based) 重试的等待秒数: 指数 + 封顶 + 均匀抖动。

    抖动区间以 base 为中心对称 ([base*(1-j/2), base*(1+j/2))),
    期望值 = base; jitter=0 时退化为确定性指数退避。
    attempt < 1 按 1 处理 (防御)。
    """
    if attempt < 1:
        attempt = 1
    base = min(initial * (2.0 ** (attempt - 1)), maximum)
    if jitter <= 0:
        return base
    factor = 1.0 - jitter / 2.0 + rng() * jitter
    return base * factor
