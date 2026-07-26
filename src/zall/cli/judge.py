"""zall.cli.judge — CLI Judge (compatibility layer, delegates to core/judge).

本文件是 v0.5.x 兼容层, 把 CLI 层的 Judge 委托到 core/judge 包。
新功能请直接在 core/judge 中实现。

两个实现 (委托):
  UndecidableJudge — 默认, 恒返回 undecidable (PR-0 诚实退让)
  SystemJudge      — --judge system, 跑 git diff + pytest, met⇔测试全过
"""

from __future__ import annotations

from zall.core.accountability import Evidence, JudgeVerdict
from zall.core.goal import TerminationState
from zall.core.judge import SystemJudge as _SystemJudge


# ── UndecidableJudge (保持不变, 核心不变量) ──


class UndecidableJudge:
    """default Judge: 恒return undecidable (PR-0 诚实退让)。

    无 Refiner (§3.3) 时, agent 无法真正理解 goal, 无法判定 met。
    与其假装 met, 不如诚实退让 undecidable —— 这是 PR-0 的落地。

    judge_type = "user" (§5.2: UNKNOWN goal_type 的 main judge 是 user;
                         但此处不判 goal, 诚实退让, 标 user 表示"需人来判")
    """

    __test__ = False

    @property
    def judge_type(self) -> str:
        return "user"

    def __call__(self, evidence: Evidence) -> JudgeVerdict:
        return JudgeVerdict(
            state=TerminationState.UNDECIDABLE,
            report="no judge",
        )


# ── SystemJudge (委托到 core/judge) ──


class SystemJudge(_SystemJudge):
    """system Judge: 跑 git + pytest judgment (§5.2 system subject)。

    委托到 zall.core.judge.SystemJudge, 保持向后兼容接口。
    """

    __test__ = False

    def __init__(
        self,
        *,
        cwd: str | None = None,
        test_cmd: list[str] | None = None,
        run_tests: bool = True,
    ) -> None:
        super().__init__(cwd=cwd, test_cmd=test_cmd, run_tests=run_tests, run_lint=False)


__all__ = ["UndecidableJudge", "SystemJudge"]