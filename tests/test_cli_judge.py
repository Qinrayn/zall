"""CLI Judge invariant test (§5.2 + §5.3 + §3.2.2 + PR-0).

IPR-0: each test must contain a counterexample.

Protected core invariants:
  1. UndecidableJudge always returns undecidable (PR-0 诚实退让)
  2. SystemJudge non- git 仓库 → undecidable (不假装能判)
  3. SystemJudge 无test → undecidable
  4. SystemJudge test全过 → met
  5. SystemJudge test有fail → not_met
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from zall.core.accountability import Evidence, Judge
from zall.core.goal import TerminationState
from zall.cli.judge import UndecidableJudge, SystemJudge


def _evidence() -> Evidence:
    """AgentLoop 传给 Judge 的 S0 占位 Evidence."""
    return Evidence(baseline_sha="s0_baseline", current_sha="s0_current")


# ──────────────────────────────────────────────────────────────────────────
# UndecidableJudge
# ──────────────────────────────────────────────────────────────────────────


class TestUndecidableJudge:
    def test_always_undecidable(self) -> None:
        """Happy path: always returns undecidable (PR-0 诚实退让)."""
        j = UndecidableJudge()
        v = j(_evidence())
        assert v.state == TerminationState.UNDECIDABLE

    def test_never_met(self) -> None:
        """Counterexample: UndecidableJudge 永不returns met (不假装完成)."""
        j = UndecidableJudge()
        for _ in range(5):
            assert j(_evidence()).state != TerminationState.MET

    def test_judge_type(self) -> None:
        """Happy path: judge_type = 'user'."""
        assert UndecidableJudge().judge_type == "user"

    def test_satisfies_protocol(self) -> None:
        """Happy path: 满足 Judge Protocol (isinstance)."""
        assert isinstance(UndecidableJudge(), Judge)


# ──────────────────────────────────────────────────────────────────────────
# SystemJudge
# ──────────────────────────────────────────────────────────────────────────


class TestSystemJudge:
    def test_satisfies_protocol(self) -> None:
        """Happy path: 满足 Judge Protocol."""
        assert isinstance(SystemJudge(), Judge)

    def test_judge_type(self) -> None:
        """Happy path: judge_type = 'system'."""
        assert SystemJudge().judge_type == "system"

    def test_non_git_undecidable(self, tmp_path: Path) -> None:
        """Counterexample: non- git 仓库 → undecidable (诚实退让, 不假装能判).

        PR-0: 无法建立 baseline → 不能判 met.
        """
        j = SystemJudge(cwd=str(tmp_path), run_tests=False)
        v = j(_evidence())
        assert v.state == TerminationState.UNDECIDABLE
        assert "git" in v.report.lower() or "baseline" in v.report.lower()

    def test_git_no_tests_undecidable(self, tmp_path: Path) -> None:
        """Counterexample: git 仓库但无test → undecidable.

        PR-0: 没有test就不能证明 goal met.
        """
        # init化 git 仓库
        subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, timeout=10)
        subprocess.run(["git", "config", "user.email", "t@t.t"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, capture_output=True)
        (tmp_path / "README.md").write_text("hello", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, capture_output=True, timeout=10)

        j = SystemJudge(cwd=str(tmp_path))
        v = j(_evidence())
        assert v.state == TerminationState.UNDECIDABLE
        assert "test" in v.report.lower()

    def test_git_tests_pass_met(self, tmp_path: Path) -> None:
        """Happy path: git 仓库 + test全过 → met."""
        subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, timeout=10)
        subprocess.run(["git", "config", "user.email", "t@t.t"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, capture_output=True)
        (tmp_path / "test_x.py").write_text(
            "def test_ok():\n    assert 1 == 1\n", encoding="utf-8"
        )
        subprocess.run(["git", "add", "."], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, capture_output=True, timeout=10)

        j = SystemJudge(cwd=str(tmp_path))
        v = j(_evidence())
        assert v.state == TerminationState.MET

    def test_git_tests_fail_not_met(self, tmp_path: Path) -> None:
        """Counterexample: git 仓库 + testfail → not_met (不假装 met)."""
        subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, timeout=10)
        subprocess.run(["git", "config", "user.email", "t@t.t"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, capture_output=True)
        (tmp_path / "test_fail.py").write_text(
            "def test_bad():\n    assert 1 == 2\n", encoding="utf-8"
        )
        subprocess.run(["git", "add", "."], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, capture_output=True, timeout=10)

        j = SystemJudge(cwd=str(tmp_path))
        v = j(_evidence())
        assert v.state == TerminationState.NOT_MET
        assert "fail" in v.report.lower()
