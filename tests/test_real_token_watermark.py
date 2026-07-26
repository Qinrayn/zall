"""真实 token 水位计数不变量 (PARADIGM Step 0: use API usage as ground truth)。

不变量 (each with counterexample):
  I   real_tokens 达临界 → "force" 压缩, 即使消息很少 (真实 usage 覆盖字符估算)。
  II  real_tokens 很低 → None (不压缩)。
  III real_tokens=None → 回退字符估算 (向后兼容, 不崩)。
  IV  优先级: 少量小消息 (估算低) + real_tokens=window → 仍 force (反例: 不被估算误导)。
"""

from __future__ import annotations

from zall.core.compactor import WatermarkMonitor
from zall.core.model import Message

_STEP = 99999  # 远离 _last_compaction_step, 避开防抖


def _wm() -> WatermarkMonitor:
    return WatermarkMonitor()


class TestRealTokenWatermark:
    def test_high_real_tokens_forces(self) -> None:
        wm = _wm()
        window = wm.get_window_size("gpt-4o")
        msgs = [Message(role="user", content="hi")]         # 估算极小
        action = wm.check_watermark(msgs, "gpt-4o", _STEP, real_tokens=window)
        assert action == "force"                             # I / IV: 真实 usage 覆盖估算

    def test_low_real_tokens_none(self) -> None:
        wm = _wm()
        window = wm.get_window_size("gpt-4o")
        msgs = [Message(role="user", content="hi")]
        action = wm.check_watermark(msgs, "gpt-4o", _STEP, real_tokens=int(window * 0.3))
        assert action is None                                # II 反例: 低占用不压缩

    def test_none_falls_back_to_estimate(self) -> None:
        wm = _wm()
        msgs = [Message(role="user", content="short")]
        # real_tokens=None → 走字符估算; 小上下文 → None, 且不报错
        assert wm.check_watermark(msgs, "gpt-4o", _STEP, real_tokens=None) is None   # III

    def test_backward_compatible_no_arg(self) -> None:
        wm = _wm()
        msgs = [Message(role="user", content="short")]
        # 不传 real_tokens (旧签名) 仍工作
        assert wm.check_watermark(msgs, "gpt-4o", _STEP) is None
