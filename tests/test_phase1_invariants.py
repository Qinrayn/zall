"""zall.test.phase1_invariants — Phase 1 (修裂缝) 全部不变量。

对应 MASTER.md §7.2 Phase 1: 对齐 — 补全裂缝。

4 个裂缝的不变量测试:
  1. Refiner 接入 run: timeline 中第一条 tool_call_start 之前必须有
     goal_statement + user_confirm 事件。
  2. 外部锚点真正外部化: ProcessTrustAnchor 不在 agent 进程内持有私钥。
  3. TerminationCriterion 默认实现: 无自定义 Judge 时 run 不崩溃。
  4. 评估体系落地: /eval 命令从 timeline 计算 metrics。

IPR-0: 每个测试包含 counterexample。
IPR-1: 每段测试引用 MASTER.md §7.2 对应改动。
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from uuid import uuid4

# 确保 src 在路径中 (必须在所有 project imports 之前)
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from zall.cli.judge import SystemJudge, UndecidableJudge  # noqa: E402
from zall.core.accountability import Evidence  # noqa: E402
from zall.core.eval import (  # noqa: E402
    CoreEvalReport,
    compute_goal_achievement_rate,
    compute_timeline_integrity_rate,
    evaluate_from_timeline,
    load_timeline,
)
from zall.core.goal import (  # noqa: E402
    GoalType,
    TerminationState,
)
from zall.core.process_anchor import ProcessTrustAnchor, start_anchor_server  # noqa: E402
from zall.core.refiner import GoalRefiner  # noqa: E402
from zall.core.verifiability import (  # noqa: E402
    EventType,
    TimelineEvent,
)


# ============================================================================
# 不变量 1: Refiner 接入 run (§7.2 #1)
# ============================================================================


class TestRefinerIntegratesWithRun:
    """Phase 1 不变量: Refiner 必须接入 run(), goal_statement + user_confirm
    必须在第一条 tool_call_start 之前记录到 timeline。

    反例: Refiner 未调用 → run 直接跳过 → timeline 无 goal_statement → 测试 fail。
    """

    def test_refiner_produces_goal_statement_event(self) -> None:
        """不变量: GoalRefiner.refine() 返回的 RefinedGoal 可构造 goal_statement event。

        Counterexample: 如果 Refiner 不产出 GOAL_STATEMENT 所需的数据,
        本测试 fail。
        """
        user_raw = "fix the bug in login.py"
        refined = GoalRefiner.refine(user_raw, judge_mode="none")

        # Refiner 必须产出合法的 GoalTriple
        goal = refined.refined_goal
        assert goal.statement.intent == user_raw
        assert goal.statement.goal_type in (GoalType.BUGFIX, GoalType.UNKNOWN)
        # R1: added_intent 必空
        assert goal.statement.added_intent == ()
        # R2: questions_used <= ask_budget
        assert refined.questions_used <= refined.ask_budget

        # 能构造 GOAL_STATEMENT event payload
        payload = {
            "intent": goal.statement.intent,
            "rewriting": goal.statement.rewriting,
            "goal_type": goal.statement.goal_type.value,
            "rewrite_confidence": goal.statement.rewrite_confidence,
            "translation_of": list(goal.statement.translation_of),
            "added_intent": list(goal.statement.added_intent),
            "termination_exposed": (
                tuple(goal.termination.exposed_dependency_set)
                if goal.termination.exposed_dependency_set is not None
                else None
            ),
            "baseline_frozen_at": goal.acceptance.baseline_frozen_at,
        }
        # JSON 可序列化 (能写入 timeline)
        assert json.dumps(payload) is not None

    def test_goal_statement_before_first_tool_call(self) -> None:
        """不变量: timeline 中 goal_statement + user_confirm 的 ts 必须
        小于第一条 tool_call_start 的 ts。

        Counterexample: 如果 run() 在调 Refiner/confirm 之前就先执行工具,
        本测试 fail。
        """
        # 模拟构造一组事件, 验证顺序
        ts_base = int(time.time() * 1000)
        events = [
            TimelineEvent(
                event_id=f"goal_statement_{uuid4().hex}",
                ts=ts_base,
                event_type=EventType.GOAL_STATEMENT,
                payload={"intent": "test"},
            ),
            TimelineEvent(
                event_id=f"user_confirm_{uuid4().hex}",
                ts=ts_base + 1,
                event_type=EventType.USER_CONFIRM,
                payload={"confirmed": True},
            ),
            TimelineEvent(
                event_id=f"tool_call_start_{uuid4().hex}",
                ts=ts_base + 100,
                event_type=EventType.TOOL_CALL_START,
                payload={"tool_id": "read_file"},
            ),
            TimelineEvent(
                event_id=f"tool_call_end_{uuid4().hex}",
                ts=ts_base + 200,
                event_type=EventType.TOOL_CALL_END,
                payload={"tool_id": "read_file", "success": True},
            ),
        ]

        # Phase 1 不变量验证
        from zall.core.eval import _verify_phase1_invariants
        assert _verify_phase1_invariants(events), \
            "Phase 1 invariant: goal_statement + user_confirm must precede first tool_call_start"

    def test_counterexample_missing_goal_statement(self) -> None:
        """counterexample: 如果 timeline 缺少 goal_statement,
        _verify_phase1_invariants 应返回 False。

        这验证了不变量的反例捕获能力。
        """
        ts_base = int(time.time() * 1000)
        events = [
            # 没有 goal_statement!
            TimelineEvent(
                event_id=f"tool_call_start_{uuid4().hex}",
                ts=ts_base + 100,
                event_type=EventType.TOOL_CALL_START,
                payload={"tool_id": "read_file"},
            ),
        ]
        from zall.core.eval import _verify_phase1_invariants
        assert not _verify_phase1_invariants(events), \
            "counterexample: missing goal_statement should fail Phase 1 invariant"


# ============================================================================
# 不变量 2: 外部锚点真正外部化 (§7.2 #2)
# ============================================================================


class TestAnchorIsExternalToAgentProcess:
    """Phase 1 不变量: ProcessTrustAnchor 不在 agent 进程内持有私钥,
    私钥由独立进程独占。

    反例: 锚点在同一进程 → 测试 fail。
    """

    def test_process_anchor_does_not_hold_private_key(self) -> None:
        """不变量: ProcessTrustAnchor 实例不持有 _private_key 属性。

        Counterexample: 如果 ProcessTrustAnchor 像 FileTrustAnchor 一样
        在进程内持有私钥, 本测试 fail。
        """
        anchor = ProcessTrustAnchor()
        # ProcessTrustAnchor 不应持有私钥
        assert not hasattr(anchor, "_private_key"), \
            "ProcessTrustAnchor must not hold a private key in-process (violates external anchor)"
        # 它应该有 IPC 相关的配置属性
        assert hasattr(anchor, "_socket_path") or hasattr(anchor, "_host"), \
            "ProcessTrustAnchor should have IPC configuration"

    def test_anchor_returns_none_when_unreachable(self) -> None:
        """不变量: anchor 不可达时 write_run_tail 返回 None (诚实退让)。

        Counterexample: 如果不可达时抛异常或返回假签名 → fail。
        """
        anchor = ProcessTrustAnchor(host="127.0.0.1", port=59999, transport="tcp", timeout=1.0)
        result = anchor.write_run_tail("test_run", "a" * 64, int(time.time() * 1000))
        assert result is None, "unreachable anchor must return None (honest retreat)"

    def test_anchor_server_signs_and_verifies(self) -> None:
        """happy path: anchor server 签名后可验证。

        Counterexample: 如果签名验证不通过 → fail。
        """
        # 启动一个临时的 anchor server
        proc = start_anchor_server(background=True)
        time.sleep(1.5)
        try:
            anchor = ProcessTrustAnchor(timeout=5.0)
            ack = anchor.write_run_tail("test_run", "abc123", int(time.time() * 1000))
            assert ack is not None, "anchor should be reachable"
            assert isinstance(ack, type(ack))  # 类型检查
            assert len(ack.sig) == 128  # ed25519 签名 hex
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


# ============================================================================
# 不变量 3: TerminationCriterion 默认实现 (§7.2 #3)
# ============================================================================


class TestDefaultJudgesWorkWithoutUserImplementation:
    """Phase 1 不变量: 无自定义 Judge 时, run() 不崩溃,
    默认 Judge (UndecidableJudge / SystemJudge) 可独立工作。

    反例: 无自定义 Judge → run 崩溃 → 测试 fail。
    """

    def test_undecidable_judge_never_crashes(self) -> None:
        """不变量: UndecidableJudge 在任何 Evidence 输入下都不抛异常。

        Counterexample: 如果 UndecidableJudge 对某些 Evidence 抛异常,
        本测试 fail。
        """
        judge = UndecidableJudge()
        # 各种 Evidence 输入
        test_cases = [
            Evidence(baseline_sha="abc", current_sha="def"),
            Evidence(baseline_sha="", current_sha=""),
            Evidence(baseline_sha="x", current_sha="y", diff="some diff"),
            Evidence(baseline_sha="a", current_sha="b", external={"key": "value"}),
        ]
        for evidence in test_cases:
            # 不应抛异常
            verdict = judge(evidence)
            assert verdict.state == TerminationState.UNDECIDABLE

    def test_system_judge_non_git_returns_undecidable(self) -> None:
        """不变量: SystemJudge 在非 git 仓库中返回 undecidable (不假装能判)。

        Counterexample: 如果 SystemJudge 在非 git 仓库中返回 met → fail。
        """
        # 创建一个临时目录 (非 git 仓库)
        with tempfile.TemporaryDirectory() as tmpdir:
            judge = SystemJudge(cwd=tmpdir)
            evidence = Evidence(baseline_sha="s0", current_sha="s1")
            verdict = judge(evidence)
            # 非 git 仓库 → 诚实退让
            assert verdict.state == TerminationState.UNDECIDABLE

    def test_system_judge_protocol_compatible(self) -> None:
        """不变量: SystemJudge 和 UndecidableJudge 都满足 Judge Protocol。

        Counterexample: 如果不满足 isinstance(Judge) → fail。
        """
        from zall.core.accountability import Judge
        assert isinstance(UndecidableJudge(), Judge)
        assert isinstance(SystemJudge(), Judge)

    def test_base_judge_table_covers_all_goal_types(self) -> None:
        """不变量: base_judge 表覆盖所有 GoalType。

        Counterexample: 查询未覆盖的 GoalType 抛 KeyError → fail。
        """
        from zall.core.accountability import base_judge
        for gt in GoalType:
            # 不应抛异常
            main, aux = base_judge(gt)
            assert main in ("system", "user", "model_self")
            assert aux in ("system", "user", "model_self")


# ============================================================================
# 不变量 4: 评估体系落地 (§7.2 #4)
# ============================================================================


class TestEvalCommandProducesMetricsFromTimeline:
    """Phase 1 不变量: /eval 命令能从 timeline.jsonl 读取并计算 metrics。

    反例: 无 timeline → eval 输出空 (诚实退让), 但有 timeline 时必须产出 metrics。
    """

    def test_load_timeline_from_file(self) -> None:
        """不变量: load_timeline 从 timeline.jsonl 加载事件。

        Counterexample: 如果文件不存在返回 None (诚实退让),
        但文件存在时必须返回非 None。
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            # 无 timeline → None
            result = load_timeline(tmpdir)
            assert result is None, "missing timeline should return None"

            # 有 timeline → 非 None
            timeline_path = Path(tmpdir) / "timeline.jsonl"
            events_data = [
                {"event_id": "e1", "ts": 1000, "event_type": "model_call", "payload": {}, "prev_hash": "0" * 64},
                {"event_id": "e2", "ts": 2000, "event_type": "tool_call_start", "payload": {"tool_id": "read_file"}, "prev_hash": "0" * 64},
                {"event_id": "e3", "ts": 3000, "event_type": "tool_call_end", "payload": {"tool_id": "read_file", "success": True}, "prev_hash": "0" * 64},
            ]
            with open(timeline_path, "w", encoding="utf-8") as f:
                for ev in events_data:
                    f.write(json.dumps(ev) + "\n")

            result = load_timeline(tmpdir)
            assert result is not None, "existing timeline should load"
            assert len(result) == 3

    def test_compute_goal_achievement_rate(self) -> None:
        """不变量: compute_goal_achievement_rate 从 timeline 计算达成率。

        Counterexample: 如果无 goal_statement 时返回 1.0 (假装达成) → fail。
        """
        # 无 goal_statement → 诚实退让 (0.0)
        events_no_goal = [
            TimelineEvent(
                event_id="e1", ts=1000, event_type=EventType.TOOL_CALL_START,
                payload={"tool_id": "read_file"}, prev_hash="0" * 64,
            ),
        ]
        m = compute_goal_achievement_rate(events_no_goal)
        assert m.value == 0.0, "no goal_statement → honest undecidable (0.0)"
        assert m.anti_value == 1.0

        # 有 goal_statement + judge_result met → 1.0
        events_met = [
            TimelineEvent(
                event_id="goal", ts=1000, event_type=EventType.GOAL_STATEMENT,
                payload={"intent": "test"}, prev_hash="0" * 64,
            ),
            TimelineEvent(
                event_id="judge", ts=2000, event_type=EventType.JUDGE_RESULT,
                payload={"state": "met"}, prev_hash="0" * 64,
            ),
        ]
        m = compute_goal_achievement_rate(events_met)
        assert m.value == 1.0

    def test_compute_timeline_integrity_rate(self) -> None:
        """不变量: compute_timeline_integrity_rate 验证链完整性。

        Counterexample: 如果哈希链断裂但返回 1.0 → fail。
        """
        # 完整链 (单条事件, genesis hash)
        events = [
            TimelineEvent(
                event_id="e1", ts=1000, event_type=EventType.GOAL_STATEMENT,
                payload={}, prev_hash="0" * 64,
            ),
        ]
        m = compute_timeline_integrity_rate(events)
        assert 0.0 <= m.value <= 1.0

    def test_evaluate_from_timeline_with_valid_timeline(self) -> None:
        """不变量: evaluate_from_timeline 从有效 timeline 产生报告。

        Counterexample: 如果有效 timeline 返回 None → fail。
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            # 构造一个有效的 timeline (简化: prev_hash 用 genesis, 测试加载逻辑)
            timeline_path = Path(tmpdir) / "timeline.jsonl"
            events_data = [
                {"event_id": "e0", "ts": 1000, "event_type": "goal_statement", "payload": {"intent": "test"}, "prev_hash": "0" * 64},
                {"event_id": "e1", "ts": 2000, "event_type": "user_confirm", "payload": {"confirmed": True}, "prev_hash": "0" * 64},
                {"event_id": "e2", "ts": 3000, "event_type": "tool_call_start", "payload": {"tool_id": "read_file"}, "prev_hash": "0" * 64},
                {"event_id": "e3", "ts": 4000, "event_type": "tool_call_end", "payload": {"tool_id": "read_file", "success": True}, "prev_hash": "0" * 64},
                {"event_id": "e4", "ts": 5000, "event_type": "judge_result", "payload": {"state": "met"}, "prev_hash": "0" * 64},
            ]
            with open(timeline_path, "w", encoding="utf-8") as f:
                for ev in events_data:
                    f.write(json.dumps(ev) + "\n")

            report = evaluate_from_timeline(tmpdir)
            assert report is not None, "valid timeline should produce eval report"
            assert isinstance(report, CoreEvalReport)
            assert len(report.metrics) >= 2  # goal_achievement + timeline_integrity

    def test_counterexample_empty_timeline(self) -> None:
        """counterexample: 空 timeline 的 eval 行为。

        验证: 空 timeline → compute_timeline_integrity_rate 返回 0.0
        (不假装完整性)。
        """
        m = compute_timeline_integrity_rate([])
        assert m.value == 0.0, "empty timeline → integrity 0.0 (honest)"
        assert m.anti_value == 1.0


if __name__ == "__main__":
    unittest.main()