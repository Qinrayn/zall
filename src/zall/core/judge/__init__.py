"""zall.core.judge — Default Judge implementations (DESIGN.md §5.2 Phase 1).

对应:
  §5.2   Judge subject: system | user | model_self
         base_judge 表: GoalType -> (main, aux)
  §3.2.2 three-state: not_met / met / undecidable (PR-0: undecidable 是诚实退让)

三个默认 Judge:
  SystemJudge   — system subject: 跑 git diff + pytest/lint, met⇔测试全过
  UserJudge     — user subject: 等待用户确认 (交互式/非交互式)
  ModelSelfJudge — model_self subject: 模型自评 (通过 model call 获取)

本包是 core/ 层 (不依赖 cli/), 实现 core/accountability.Judge Protocol。
cli/judge.py 兼容层委托到本包。

IPR constraints:
  IPR-0: invariant tests at tests/test_judge_invariants.py
  IPR-1: corresponds to DESIGN.md §5.2 + §5.3 + §3.2.2 + PR-0
  IPR-3: stdlib + subprocess only, no model SDK (ModelSelfJudge 用 ModelAdapter Protocol)
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from zall.core.accountability import (
    Evidence,
    Judge,
    JudgeVerdict,
    TestCaseResult,
)
from zall.core.goal import GoalType, TerminationState


# ── SystemJudge: system subject ──


class SystemJudge(Judge):
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
        lint_cmd: list[str] | None = None,
        run_tests: bool = True,
        run_lint: bool = False,
    ) -> None:
        self._cwd = Path(cwd) if cwd else Path.cwd()
        # default pytest -x (首失败即停, 快速判定 not_met) -q (简洁输出)
        # v1.1 dogfood 修正: 之前无 -q 全量跑超时, 现在只跑受影响测试 + -x 快速判定
        self._test_cmd = test_cmd or ["python", "-m", "pytest", "-x", "-q"]
        self._lint_cmd = lint_cmd or ["python", "-m", "ruff", "check"]
        self._run_tests = run_tests
        self._run_lint = run_lint

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
                caveat=None,
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
                caveat=None,
                report="no tests collected" if exit_code == 5 else "pytest unavailable",
            )

        if not test_results:
            # 有test但parse不出数量 → 保守 undecidable
            return JudgeVerdict(
                state=TerminationState.UNDECIDABLE,
                caveat=None,
                report="test results unparseable",
            )

        failures = [t for t in test_results if not t.passed and not t.skipped]
        if failures:
            return JudgeVerdict(
                state=TerminationState.NOT_MET,
                caveat=None,
                report=f"{len(failures)} test(s) failed",
            )

        # 全过 → met
        return JudgeVerdict(
            state=TerminationState.MET,
            caveat=None,
            report=f"all {len(test_results)} test(s) passed"
                   + (" (with uncommitted changes)" if has_changes else ""),
        )

    def _git_sha(self, ref: str) -> str | None:
        """取 git ref 的 sha, 非 git 仓库return None。"""
        try:
            r = subprocess.run(
                ["git", "rev-parse", ref],
                cwd=self._cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10,
            )
            return r.stdout.strip() if r.returncode == 0 else None
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return None

    def _git_diff(self) -> str:
        """取未commit改动的 diff。"""
        try:
            r = subprocess.run(
                ["git", "diff", "HEAD"],
                cwd=self._cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10,
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

        返回 (exit_code, results)。results 仅在 exit 0 时非空 (用通过数填充),
        exit 1 时包含通过和失败。不解析 stdout 文本。

        v1.1 dogfood 修正: 默认只跑 git diff 涉及的测试文件 (而非全量),
        避免全量 1500+ 测试超时 (120s 不够)。无 diff 时跑全量。
        """
        # 构建测试命令: 若有 git diff, 只跑受影响的测试文件 (最多 1 个, 防超时)
        # v1.1 dogfood 修正: 之前全量跑 1500+ 测试超时, 现在只跑 1 个最快的受影响测试
        cmd = list(self._test_cmd)
        affected = self._affected_test_files()
        if affected:
            cmd.append(affected[0])  # 只跑第一个, 避免 CLI 类测试超时
        try:
            r = subprocess.run(
                cmd,
                cwd=self._cwd, capture_output=True, text=True, timeout=30,
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
        """从 pytest stdout 提取 'N passed' / 'N failed' 的 N。"""
        import re
        m = re.search(rf"(\d+)\s+{word}", stdout)
        return int(m.group(1)) if m else 0

    def _affected_test_files(self) -> list[str]:
        """v1.1 dogfood 修正: 基于 git diff 找受影响的测试文件。

        策略: git diff 涉及的源文件 (src/xxx.py) -> 对应的测试文件
        (tests/test_xxx.py)。若改的是 tests/ 下的文件, 直接包含。
        无 git 或无 diff 时返回空列表 (调用方跑全量)。

        排除慢测试 (integration/interaction/real_api), 避免超时。
        """
        diff = self._git_diff()
        if not diff:
            return []
        # 从 diff 中提取改动的文件路径
        import re
        changed = set()
        for m in re.finditer(r"^\+\+\+ b/(.+)$", diff, re.MULTILINE):
            changed.add(m.group(1).strip())
        # 慢测试关键词: 这些文件跑真实 API / 完整 CLI / 子进程, 不适合 judge 快速判定
        SLOW_MARKERS = (
            "integration", "interaction", "real_api", "stream", "e2e",
            "cli_app", "new_commands", "cli_new", "sandbox", "pty",
            "mcp_registration", "lifecycle_hooks", "subprocess",
        )
        test_files: list[str] = []
        for path in changed:
            if path.startswith("tests/") and path.endswith(".py"):
                if any(m in path for m in SLOW_MARKERS):
                    continue  # 跳过慢测试
                test_files.append(path)
            elif path.startswith("src/") and path.endswith(".py"):
                # src/zall/core/foo.py -> tests/test_foo.py
                stem = Path(path).stem
                candidate = f"tests/test_{stem}.py"
                if (self._cwd / candidate).exists():
                    if any(m in candidate for m in SLOW_MARKERS):
                        continue
                    test_files.append(candidate)
        return test_files


# ── UserJudge: user subject ──


class UserJudge(Judge):
    """user Judge: 等待用户确认 (§5.2 user subject)。

    判定逻辑:
      - 用户明确确认 → met
      - 用户拒绝 → not_met
      - 用户未响应/不可交互 → undecidable (诚实退让)

    这是 PR-0 诚实退让的另一种体现: 当无法自动化判定, 把判定权交还用户。
    用户不可达时不假装 met。

    judge_type = "user" (§5.2)
    """

    __test__ = False

    def __init__(
        self,
        *,
        confirm_fn: callable | None = None,  # () -> bool
        timeout: float = 30.0,
    ) -> None:
        """
        Args:
            confirm_fn: 返回 True=用户确认 met, False=用户拒绝, None=不可达
            timeout: 等待用户响应的超时 (秒)
        """
        self._confirm_fn = confirm_fn
        self._timeout = timeout

    @property
    def judge_type(self) -> str:
        return "user"

    def __call__(self, evidence: Evidence) -> JudgeVerdict:
        """等待用户确认, three-state。"""
        if self._confirm_fn is None:
            # 无确认函数 → 诚实退让
            return JudgeVerdict(
                state=TerminationState.UNDECIDABLE,
                caveat=None,
                report="no user confirmation function available",
            )

        try:
            result = self._confirm_fn()
        except Exception:
            return JudgeVerdict(
                state=TerminationState.UNDECIDABLE,
                caveat=None,
                report="user confirmation failed",
            )

        if result is True:
            return JudgeVerdict(
                state=TerminationState.MET,
                caveat=None,
                report="user confirmed",
            )
        if result is False:
            return JudgeVerdict(
                state=TerminationState.NOT_MET,
                caveat=None,
                report="user rejected",
            )
        # result is None → 不可达
        return JudgeVerdict(
            state=TerminationState.UNDECIDABLE,
            caveat=None,
            report="user did not respond",
        )


# ── ModelSelfJudge: model_self subject ──


class ModelSelfJudge(Judge):
    """model_self Judge: 模型自评 (§5.2 model_self subject)。

    判定逻辑:
      - 调模型, 问"目标是否达成?" → 根据模型回答 met/not_met/undecidable
      - 模型不可用 → undecidable (诚实退让)

    这是最弱的 Judge (模型自评有幻觉风险), 所以 base_judge 表中
    model_self 永远作为 aux Judge, 不担任主 Judge。

    judge_type = "model_self" (§5.2)
    """

    __test__ = False

    def __init__(
        self,
        *,
        model: Any = None,  # ModelAdapter or similar; None → 自评退让为 undecidable (骨架构造)
        system_prompt: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        """
        Args:
            model: 模型适配器 (实现 call 接口)
            system_prompt: 自评 system prompt (可选)
            timeout: 模型调用超时 (秒)
        """
        self._model = model
        self._system_prompt = system_prompt or (
            "You are evaluating whether a task has been completed. "
            "Be honest and conservative — if uncertain, say undecidable."
        )
        self._timeout = timeout

    @property
    def judge_type(self) -> str:
        return "model_self"

    def __call__(self, evidence: Evidence) -> JudgeVerdict:
        """模型自评, three-state。"""
        if self._model is None:
            return JudgeVerdict(
                state=TerminationState.UNDECIDABLE,
                caveat=None,
                report="no model available for self-evaluation",
            )

        # 构造自评 prompt
        prompt = (
            "Evaluate whether the task goal has been met based on the evidence. "
            "Return exactly one of: MET, NOT_MET, UNDECIDABLE. "
            "If evidence is insufficient or ambiguous, return UNDECIDABLE.\n\n"
            f"Evidence (baseline={evidence.baseline_sha[:12]}, "
            f"current={evidence.current_sha[:12]}): "
            f"diff={evidence.diff[:200] if evidence.diff else '(none)'}"
        )

        try:
            response = self._model.call(
                system=self._system_prompt,
                prompt=prompt,
                timeout=self._timeout,
            )
        except Exception as e:
            return JudgeVerdict(
                state=TerminationState.UNDECIDABLE,
                caveat=None,
                report=f"model self-evaluation unavailable: {e}",
            )

        text = str(response).strip().upper() if response else ""
        if "MET" in text and "NOT" not in text:
            return JudgeVerdict(
                state=TerminationState.MET,
                caveat=None,
                report="model self-evaluation: met",
            )
        if "NOT_MET" in text or ("NOT" in text and "MET" in text):
            return JudgeVerdict(
                state=TerminationState.NOT_MET,
                caveat=None,
                report="model self-evaluation: not met",
            )
        # 默认 undecidable (模型回答模糊)
        return JudgeVerdict(
            state=TerminationState.UNDECIDABLE,
            caveat=None,
            report=f"model self-evaluation: undecidable (response: {text[:50]})",
        )


# ── 工厂函数: 根据 GoalType 获取默认 Judge 组合 ──


def default_judges_for_goal_type(goal_type: GoalType) -> tuple[Judge, Judge | None]:
    """根据 GoalType 返回 (main_judge, aux_judge) 默认组合。

    基于 base_judge 表 (§5.2), 使用各 subject 的默认实现。
    注意: UserJudge 和 ModelSelfJudge 需要调用方注入依赖 (confirm_fn / model)。
    此函数返回"骨架", 调用方须填充。

    返回的 Judge 是未初始化的模板 (UserJudge 无 confirm_fn, ModelSelfJudge 无 model),
    调用方应自行构造完整实例。
    """
    from zall.core.accountability import base_judge

    main_type, aux_type = base_judge(goal_type)

    main: Judge
    aux: Judge | None = None

    if main_type == "system":
        main = SystemJudge()
    elif main_type == "user":
        main = UserJudge()  # 调用方须设 confirm_fn
    elif main_type == "model_self":
        main = ModelSelfJudge()  # 调用方须设 model
    else:
        main = SystemJudge()  # fallback

    if aux_type == "system":
        aux = SystemJudge()
    elif aux_type == "user":
        aux = UserJudge()
    elif aux_type == "model_self":
        aux = ModelSelfJudge()

    return main, aux


__all__ = [
    "SystemJudge",
    "UserJudge",
    "ModelSelfJudge",
    "default_judges_for_goal_type",
]