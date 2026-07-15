"""zall.cli.judge — 把 §5.2 Judge 接进 CLI。

Corresponds to:
  §5.2   Judge subject: system | user | model_self
         base_judge(GoalType) -> (main, aux)
  §5.3   Evidence: baseline_sha / current_sha / diff / test_results / lint_results
  §3.2.2 three-state: not_met / met / undecidable (PR-0: undecidable 是诚实退让)

本模块是应用层 (非 core/), 实现 core/accountability.Judge Protocol。
core/ 不依赖本文件; 本文件依赖 core/。

两个实现:
  UndecidableJudge — 默认, 恒返回 undecidable (PR-0 诚实退让, 无 Refiner 时安全)
  SystemJudge      — --judge system, 跑 git diff + pytest, met⇔测试全过

IPR constraints:
  IPR-0: invariant tests at tests/test_cli_judge.py
  IPR-1: corresponds to DESIGN.md §5.2 + §5.3 + §3.2.2 + PR-0
  IPR-3: only stdlib + subprocess, no model SDK
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from zall.core.accountability import (
    Evidence,
    Judge,
    JudgeVerdict,
    LintResult,
    TestCaseResult,
)
from zall.core.goal import TerminationState


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


class SystemJudge:
    """system Judge: 跑 git + pytest judgment (§5.2 system subject)。

    判定逻辑 (§3.2.2 three-state):
      - 有测试且全过 → met
      - 有测试且有失败 → not_met
      - 无测试 / 非 git 仓库 / pytest 不可用 → undecidable (诚实退让)

    Evidence 采集:
      AgentLoop 传的 Evidence 是 S0 占位 sha (s0_baseline/s0_current)。
      SystemJudge 自己采集真实 git sha + pytest 结果, 覆盖占位字段。
      这是诚实的: Judge 是应用层, 有权自己采集证据。

    judge_type = "system" (§5.2)
    """

    __test__ = False

    def __init__(
        self,
        *,
        cwd: str | None = None,
        test_cmd: list[str] | None = None,
        run_tests: bool = True,
    ) -> None:
        self._cwd = Path(cwd) if cwd else Path.cwd()
        # default pytest -q; 可覆盖 (eg. ["pytest", "-x", "-q"])
        self._test_cmd = test_cmd or ["python", "-m", "pytest", "-q"]
        self._run_tests = run_tests

    @property
    def judge_type(self) -> str:
        return "system"

    def __call__(self, evidence: Evidence) -> JudgeVerdict:
        """采集真实evidence, judgment three-state。"""
        # 采集 git sha
        baseline = self._git_sha("HEAD")
        if baseline is None:
            # 非 git 仓库 → 无法judgment (诚实退让)
            return JudgeVerdict(
                state=TerminationState.UNDECIDABLE,
                report="not a git repository — cannot establish baseline",
            )

        # 采集 diff (是否有改动)
        diff = self._git_diff()
        has_changes = bool(diff.strip())

        # 采集test结果
        exit_code, test_results = self._run_pytest() if self._run_tests else (-1, [])

        # exit_code 5 = 无test收集 → undecidable
        # exit_code < 0 = pytest 不可用 → undecidable
        if exit_code == 5 or exit_code < 0:
            return JudgeVerdict(
                state=TerminationState.UNDECIDABLE,
                report="no tests collected" if exit_code == 5 else "pytest unavailable",
            )

        if not test_results:
            # 有test但parse不出数量 → 保守 undecidable
            return JudgeVerdict(
                state=TerminationState.UNDECIDABLE,
                report="test results unparseable",
            )

        failures = [t for t in test_results if not t.passed and not t.skipped]
        if failures:
            return JudgeVerdict(
                state=TerminationState.NOT_MET,
                report=f"{len(failures)} test(s) failed",
            )

        # 全过 → met
        return JudgeVerdict(
            state=TerminationState.MET,
            report=f"all {len(test_results)} test(s) passed"
                   + (" (with uncommitted changes)" if has_changes else ""),
        )

    def _git_sha(self, ref: str) -> str | None:
        """取 git ref 的 sha, 非 git 仓库return None。"""
        try:
            r = subprocess.run(
                ["git", "rev-parse", ref],
                cwd=self._cwd, capture_output=True, text=True, timeout=10,
            )
            return r.stdout.strip() if r.returncode == 0 else None
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return None

    def _git_diff(self) -> str:
        """取未commit改动的 diff。"""
        try:
            r = subprocess.run(
                ["git", "diff", "HEAD"],
                cwd=self._cwd, capture_output=True, text=True, timeout=10,
            )
            return r.stdout if r.returncode == 0 else ""
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return ""

    def _run_pytest(self) -> tuple[int, list[TestCaseResult]]:
        """跑 pytest, 用exit码judgment (比parse stdout 文本可靠)。

        pytest 退出码:
          0 = 全部通过
          1 = 有失败/错误
          2 = 中断
          5 = 无测试收集
          其他 = 不可用

        返回 (exit_code, results)。results 仅在 exit 0 时非空 (用通过数填充)。
        不解析 stdout 文本 —— 文本里的字母会被误当测试状态字符。
        """
        try:
            r = subprocess.run(
                self._test_cmd,
                cwd=self._cwd, capture_output=True, text=True, timeout=120,
                encoding="utf-8", errors="replace",
            )
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return (-1, [])

        exit_code = r.returncode
        # 从 "N passed" / "N failed" 行提取数量 (仅用于填充 results, 不参与judgment)
        count = self._parse_count(r.stdout, "passed")
        results: list[TestCaseResult] = []
        if exit_code == 0 and count > 0:
            results = [TestCaseResult(test_id=f"test_{i}", passed=True) for i in range(count)]
        elif exit_code == 1:
            failed = self._parse_count(r.stdout, "failed")
            passed = self._parse_count(r.stdout, "passed")
            results = (
                [TestCaseResult(test_id=f"pass_{i}", passed=True) for i in range(passed)]
                + [TestCaseResult(test_id=f"fail_{i}", passed=False) for i in range(failed)]
            )
        return (exit_code, results)

    @staticmethod
    def _parse_count(stdout: str, word: str) -> int:
        """从 pytest stdout 提取 'N passed' / 'N failed' 的 N。

        pytest 汇总行形如: "8 passed in 0.22s" 或 "1 failed, 7 passed in 0.5s"
        """
        import re
        m = re.search(rf"(\d+)\s+{word}", stdout)
        return int(m.group(1)) if m else 0
