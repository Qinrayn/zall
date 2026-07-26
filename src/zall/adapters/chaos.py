"""zall.adapters.chaos — 故障注入包装 adapter (G15, kimi _chaos provider 对标).

用途 (E2E 设施):
  - 错误恢复检验: 包装任意真实 adapter, 按概率注入 429/5xx/网络错误 —
    验证 RetryBudget/退避/重试可见性链路在真实会话中的表现。
  - 用法: env ZALL_CHAOS=0.3 (概率) [+ ZALL_CHAOS_MODES=429,500,transport]
    (cli.config._build_adapter 构建后自动包装)。

设计:
  - 故障形态与真实错误路径完全同构:
      "429"/"500"/"503" → ModelResponse(raw={"status": N}) — 与
        BaseAdapter.make_error_response 同形, 走 API 重试路径;
      "transport" → raise httpx.ConnectError — 属 RETRYABLE_EXC, 走网络重试路径。
  - 前进性护栏: 连续注入 max_consecutive 次后强制放行一次 —
    概率再高也不会把会话锁死 (E2E 必须能收敛)。
  - rng 可注入 (random.Random(seed)) — 回归测试确定性。

IPR constraints:
  IPR-0: tests/test_scripted_chaos_invariants.py (含反例)
  IPR-3: stdlib + httpx (adapters 层既有依赖), 无模型 SDK
"""

from __future__ import annotations

import random
from typing import Any, Iterator

import httpx

from zall.core.model import ModelResponse, StopReason

# 支持的故障模式 → 注入形态
_API_MODES = {"429", "500", "503"}
_DEFAULT_MODES = ("429", "500", "transport")


class ChaosAdapter:
    """包装真实 adapter, 按概率注入故障; 未注入时全量透传。

    stats: {"injected": N, "passed": M} — 供测试/E2E 报告断言注入确实发生。
    """

    __test__ = False

    def __init__(
        self,
        inner: Any,
        *,
        probability: float = 0.3,
        modes: tuple[str, ...] = _DEFAULT_MODES,
        max_consecutive: int = 2,
        rng: random.Random | None = None,
    ) -> None:
        if not 0.0 <= probability <= 1.0:
            raise ValueError(f"chaos: probability 须在 [0,1], 得到 {probability}")
        bad = [m for m in modes if m not in _API_MODES and m != "transport"]
        if bad:
            raise ValueError(f"chaos: 未知故障模式 {bad} (可用: 429/500/503/transport)")
        self._inner = inner
        self._probability = probability
        self._modes = tuple(modes) or _DEFAULT_MODES
        self._max_consecutive = max(1, max_consecutive)
        self._rng = rng if rng is not None else random.Random()
        self._consecutive = 0
        self.stats = {"injected": 0, "passed": 0}

    # ── 故障决策 ──

    def _maybe_inject(self) -> ModelResponse | None:
        """返回注入的错误响应 / raise 网络异常 / None=放行。"""
        if self._consecutive >= self._max_consecutive:
            # 前进性护栏: 连续注入达上限, 强制放行
            self._consecutive = 0
            self.stats["passed"] += 1
            return None
        if self._rng.random() >= self._probability:
            self._consecutive = 0
            self.stats["passed"] += 1
            return None
        self._consecutive += 1
        self.stats["injected"] += 1
        mode = self._modes[self._rng.randrange(len(self._modes))]
        if mode == "transport":
            raise httpx.ConnectError("[chaos] injected network error")
        status = int(mode)
        return ModelResponse(
            content=f"[chaos] injected HTTP {status}",
            stop_reason=StopReason.STOP,
            raw={"status": status, "body": "[chaos]", "chaos": True},
        )

    # ── ModelAdapter Protocol ──

    @property
    def model_name(self) -> str:
        return f"chaos({getattr(self._inner, 'model_name', '?')})"

    def complete(self, *args: Any, **kwargs: Any) -> ModelResponse:
        injected = self._maybe_inject()
        if injected is not None:
            return injected
        return self._inner.complete(*args, **kwargs)

    def _make_chaos_stream(self, inner_stream: Any) -> Any:
        """构造注入层流式函数 (由 __getattr__ 按需派生)。"""

        def _chaos_stream(*args: Any, **kwargs: Any) -> Iterator[Any]:
            injected = self._maybe_inject()  # transport 模式在迭代时 raise (与真实 httpx 流式同构)
            if injected is not None:
                yield "", injected
                return
            yield from inner_stream(*args, **kwargs)

        return _chaos_stream

    def close(self) -> None:
        close = getattr(self._inner, "close", None)
        if close is not None:
            close()

    def __getattr__(self, name: str) -> Any:
        # complete_stream 不定义在类上 — inner 无流式时 hasattr 探测必须为 False
        # (loop 靠 hasattr 自动降级到 complete, 不能被包装层假装有)
        if name == "complete_stream":
            return self._make_chaos_stream(getattr(self._inner, name))
        # 其余能力 (set_retry_callback/estimate_tokens/...) 全量委托 inner
        return getattr(self._inner, name)
