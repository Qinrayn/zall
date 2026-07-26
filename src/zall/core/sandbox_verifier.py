"""zall.core.sandbox_verifier — 沙盒执行验证器 (PARADIGM Step 2: grounded refutation)。

红蓝对抗里 Red 的**可复现证伪**器官: 不是让模型自评 (会自欺), 而是把候选解法
**真的在隔离沙盒里跑**, 用执行结果 (退出码 / 断言是否通过) 作为 ground truth reward。
这正是"经验时代"的 grounded reward, 也是"自改进只在可验证处成立"的落地。

  verify(solution, check=None):
    - 无 check: 解法能在沙盒干净运行 (exit 0) → verified; 报错/超时 → refuted(broken)。
    - 有 check: 写入断言脚本 import 解法并跑, 通过 → verified; 否则 refuted。

make_sandbox_red_fn(): 产出一个可直接喂给 RedBlueLoop 的 red_fn (纯执行验证)。

安全: 复用 ProcessSandbox (子进程隔离 + 超时 + 输出上限)。执行模型产出的代码本身
有风险, 与 agent 的 bash 工具同级, 且限定在 opt-in 的研究循环内。

IPR-3: stdlib + zall.sandbox (基础设施, 非模型 SDK)。zall.sandbox 惰性导入。
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass

# 提取 markdown 代码围栏内的代码 (模型常把解法包在 ```python ... ``` 里)
_FENCE_RE = re.compile(r"```(?:[a-zA-Z0-9_+-]*)\n(.*?)```", re.DOTALL)


def extract_code(text: str) -> str:
    """从文本中抽取代码: 有 ``` 围栏取围栏内容 (拼接所有块); 否则原样返回。"""
    if not text:
        return ""
    blocks = _FENCE_RE.findall(text)
    if blocks:
        return "\n\n".join(b.rstrip() for b in blocks).strip()
    return text.strip()


@dataclass(frozen=True)
class VerifyResult:
    """一次沙盒验证结果 (可复核)。"""

    score: float
    critique: str
    broken: bool
    duration: float = 0.0


class SandboxVerifier:
    """在隔离沙盒中执行候选解法并按真实结果打分 (grounded reward)。"""

    def __init__(self, *, timeout: float = 10.0) -> None:
        self._timeout = float(timeout)

    def verify(
        self, solution: str, *, check: str | None = None, task: str = "",
    ) -> VerifyResult:
        """跑解法 (+可选断言 check), 返回 VerifyResult。

        score/broken 约定: 通过 → (1.0, broken=False); 失败/报错/超时 → (0.0, broken=True)。
        空解法 → 直接 broken。
        """
        code = extract_code(solution)
        if not code.strip():
            return VerifyResult(0.0, "empty solution", True)

        # 惰性导入, 保持 core 可在无 sandbox 依赖时被导入
        from zall.sandbox import ProcessSandbox, ResourceLimits

        sb = ProcessSandbox(limits=ResourceLimits(timeout_seconds=self._timeout))
        try:
            ws = sb.create_workspace()
            (ws / "solution.py").write_text(code, encoding="utf-8")
            py = f'"{sys.executable}"'
            if check and check.strip():
                (ws / "check.py").write_text(check, encoding="utf-8")
                res = sb.execute_command(f"{py} check.py")
                what = "check"
            else:
                res = sb.execute_command(f"{py} solution.py")
                what = "run"
            if res.success:
                return VerifyResult(1.0, f"verified: {what} passed in sandbox", False,
                                    getattr(res, "duration", 0.0))
            err = (getattr(res, "error", "") or getattr(res, "output", "") or "").strip()
            # 取报错末尾 (Traceback 关键信息通常在末尾)
            tail = err.splitlines()[-1] if err else "nonzero exit"
            return VerifyResult(0.0, f"refuted ({what}): {tail[:180]}", True,
                                getattr(res, "duration", 0.0))
        finally:
            sb.cleanup()


def make_sandbox_red_fn(
    verifier: SandboxVerifier | None = None,
    check: str | None = None,
):
    """产出可喂给 RedBlueLoop 的 red_fn: 用沙盒执行验证候选解法。

    check: 可选断言脚本 (import solution 并断言); 提供则用它当 ground-truth 测试,
           否则仅验证"能干净运行"。round_idx/bank 忽略 (执行验证是客观的)。
    """
    v = verifier or SandboxVerifier()

    def red_fn(content: str, bank, round_idx: int) -> tuple[float, str, bool]:
        r = v.verify(content, check=check)
        return r.score, r.critique, r.broken

    return red_fn
