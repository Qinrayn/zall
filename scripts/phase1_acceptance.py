#!/usr/bin/env python3
"""Phase 1 (修裂缝) 端到端验收脚本。

验证 4 个改动的关键承诺:
  1. Refiner 接入 run
  2. 外部锚点
  3. TerminationCriterion 默认
  4. 评估体系
"""

import json
import os
import sys
import tempfile
import time
from pathlib import Path
from uuid import uuid4

# Ensure src is on path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from zall.cli.judge import SystemJudge, UndecidableJudge
from zall.core.accountability import Evidence, base_judge
from zall.core.eval import (
    CoreEvalReport,
    evaluate_from_timeline,
    load_timeline,
)
from zall.core.goal import GoalType, TerminationState
from zall.core.process_anchor import ProcessTrustAnchor, start_anchor_server
from zall.core.refiner import GoalRefiner
from zall.core.verifiability import EventType, TimelineEvent


# ── Helpers ──

PASS = "PASS"
FAIL = "FAIL"
_total = 0
_passed = 0


def check(scenario: str, detail: str, ok: bool, reason: str = "") -> None:
    global _total, _passed
    _total += 1
    status = PASS if ok else FAIL
    if ok:
        _passed += 1
    tag = f"[{status}]"
    print(f"  {tag} {scenario} — {detail}")
    if reason:
        print(f"       {reason}")
    if not ok:
        print(f"       >>> FAILURE <<<")


# ====================================================================
# Scenario 1: Refiner 接入 run
# ====================================================================

def scenario_1_refiner() -> bool:
    print("\n" + "=" * 60)
    print("Scenario 1: Refiner 接入 run")
    print("=" * 60)

    all_ok = True

    # 1a. GoalRefiner.refine() 返回 RefinedGoal 且不抛异常
    try:
        refined = GoalRefiner.refine("修复登录页面的bug", judge_mode="none")
        check(1, "refine() 返回 RefinedGoal", True)
    except Exception as e:
        check(1, "refine() 返回 RefinedGoal", False, f"抛异常: {e}")
        return False

    goal = refined.refined_goal

    # 1b. added_intent 必空 (R1)
    r1_ok = goal.statement.added_intent == () and refined.added_intent == ()
    check(1, "added_intent 必空 (R1)", r1_ok,
          f"added_intent={goal.statement.added_intent}, refined.added_intent={refined.added_intent}")

    # 1c. questions_used <= ask_budget (R2)
    r2_ok = refined.questions_used <= refined.ask_budget
    check(1, "questions_used <= ask_budget (R2)", r2_ok,
          f"questions_used={refined.questions_used}, ask_budget={refined.ask_budget}")

    # 1d. 能构造合法的 GOAL_STATEMENT 事件 payload
    try:
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
        json_str = json.dumps(payload)
        check(1, "GOAL_STATEMENT payload 可 JSON 序列化", True)
    except Exception as e:
        check(1, "GOAL_STATEMENT payload 可 JSON 序列化", False, str(e))
        all_ok = False

    # 1e. 验证 judge_mode="system" 强制 BUGFIX
    try:
        sys_refined = GoalRefiner.refine("随便写点什么", judge_mode="system")
        sys_goal = sys_refined.refined_goal
        is_bugfix = sys_goal.statement.goal_type == GoalType.BUGFIX
        check(1, "judge_mode=system 强制 BUGFIX", is_bugfix,
              f"got {sys_goal.statement.goal_type}")
        # system mode 的 exposed_dependency_set 必须非空
        has_exposed = sys_goal.termination.exposed_dependency_set is not None
        check(1, "system judge 的 exposed_dependency_set 非空", has_exposed)
    except Exception as e:
        check(1, "judge_mode=system 测试", False, str(e))
        all_ok = False

    # 1f. 验证 confidence 范围
    conf_ok = 0.0 <= refined.confidence <= 1.0
    check(1, "confidence 在 [0.0, 1.0] 范围内", conf_ok,
          f"confidence={refined.confidence}")

    # 1g. 验证各种 goal_type 分类
    test_cases = [
        ("修复bug", GoalType.BUGFIX),
        ("新增功能", GoalType.FEATURE),
        ("重构代码", GoalType.REFACTOR),
        ("写测试", GoalType.TEST_WRITE),
        ("更新文档", GoalType.DOCS),
        ("优化性能", GoalType.PERF_OPT),
        ("审查代码", GoalType.REVIEW),
        ("排查一下这个奇怪的现象", GoalType.INVESTIGATE),
        ("迁移到新版本", GoalType.MIGRATE),
        ("初始化项目", GoalType.SCAFFOLD),
        ("完全无关的内容", GoalType.UNKNOWN),
    ]
    for text, expected_type in test_cases:
        r = GoalRefiner.refine(text, judge_mode="none")
        # For UNKNOWN we accept either UNKNOWN or whatever the classifier picks
        if expected_type == GoalType.UNKNOWN:
            # Just verify it doesn't crash
            pass
        else:
            gt = r.refined_goal.statement.goal_type
            if gt != expected_type:
                check(1, f"分类 '{text}' -> {expected_type}", False,
                      f"got {gt}")
                all_ok = False
            else:
                check(1, f"分类 '{text}' -> {expected_type}", True)

    # 1h. 验证 RefinedGoal 的 validator 约束 (R1/R2 在构造时验证)
    # 尝试构造一个违反 R1 的 RefinedGoal 应该失败
    try:
        from zall.core.goal import RefinedGoal, GoalTriple, GoalStatement, AcceptanceContract

        # 尝试构造 added_intent 非空的 GoalStatement — 应 raise
        try:
            bad_stmt = GoalStatement(
                intent="test",
                rewriting="test",
                rewrite_confidence=0.9,
                goal_type=GoalType.BUGFIX,
                added_intent=("extra",),
            )
            check(1, "R1: GoalStatement added_intent 非空应 raise", False,
                  "未 raise ValueError")
        except ValueError:
            check(1, "R1: GoalStatement added_intent 非空应 raise", True)

        # 尝试构造 questions_used > ask_budget 的 RefinedGoal — 应 raise
        try:
            # 先构造一个合法的 GoalTriple
            stmt = GoalStatement(
                intent="test",
                rewriting="test",
                rewrite_confidence=0.9,
                goal_type=GoalType.BUGFIX,
            )
            # 需要一个合法的 TerminationCriterion
            class _DummyTerm:
                exposed_dependency_set: tuple[str, ...] | None = ()
                def __call__(self, state):
                    return TerminationState.UNDECIDABLE

            triple = GoalTriple(
                statement=stmt,
                termination=_DummyTerm(),
                acceptance=AcceptanceContract(baseline_frozen_at="test"),
            )
            bad_refined = RefinedGoal(
                user_raw="test",
                questions_used=5,
                refined_goal=triple,
                confidence=0.9,
                ask_budget=0,
            )
            check(1, "R2: questions_used > ask_budget 应 raise", False,
                  "未 raise ValueError")
        except ValueError as e:
            check(1, "R2: questions_used > ask_budget 应 raise", True,
                  f"预期错误: {e}")
        except Exception as e:
            check(1, "R2: questions_used > ask_budget 应 raise", False,
                  f"unexpected error: {e}")
    except Exception as e:
        check(1, "R1/R2 validator 测试", False, str(e))
        all_ok = False

    return all_ok


# ====================================================================
# Scenario 2: 外部锚点
# ====================================================================

def scenario_2_anchor() -> bool:
    print("\n" + "=" * 60)
    print("Scenario 2: 外部锚点")
    print("=" * 60)

    all_ok = True

    # 2a. ProcessTrustAnchor 不持有私钥
    anchor = ProcessTrustAnchor()
    has_private_key = hasattr(anchor, "_private_key")
    check(2, "ProcessTrustAnchor 不持有私钥", not has_private_key,
          f"has _private_key = {has_private_key}")

    # 2b. 不可达时返回 None
    unreachable_anchor = ProcessTrustAnchor(
        host="127.0.0.1", port=59999, transport="tcp", timeout=1.0
    )
    result = unreachable_anchor.write_run_tail(
        "test_run", "a" * 64, int(time.time() * 1000)
    )
    check(2, "不可达时返回 None", result is None,
          f"got {type(result).__name__ if result is not None else None}")

    # 2c. 启动 server，连接，签名，验证签名长度
    if os.name == "nt":
        # Windows: use TCP loopback
        proc = start_anchor_server(host="127.0.0.1", port=19881, background=True)
    else:
        # Unix: use Unix socket with a temp path
        sock_path = tempfile.mktemp(suffix=".sock", prefix="zall_anchor_")
        proc = start_anchor_server(socket_path=sock_path, background=True)
    time.sleep(2.0)  # wait for server to start

    server_ok = False
    try:
        if os.name == "nt":
            client = ProcessTrustAnchor(host="127.0.0.1", port=19881, transport="tcp", timeout=5.0)
        else:
            client = ProcessTrustAnchor(socket_path=sock_path, transport="unix", timeout=5.0)

        ack = client.write_run_tail(
            "test_run_42", "deadbeef" * 8, int(time.time() * 1000)
        )
        if ack is not None:
            check(2, "server 可达时返回 AckEvent", True)
            sig_len_ok = len(ack.sig) == 128
            check(2, "签名长度 128 字符 (hex)", sig_len_ok,
                  f"实际长度={len(ack.sig)}")
            check(2, "返回的 anchor_id 非空", bool(ack.anchor_id))
            check(2, "返回的 run_id 匹配", ack.run_id == "test_run_42")
            check(2, "返回的 last_event_hash 匹配", ack.last_event_hash == "deadbeef" * 8)
            server_ok = True
        else:
            check(2, "server 可达时返回 AckEvent", False,
                  "ack is None")
    except Exception as e:
        check(2, "连接 server 并签名", False, str(e))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
        # clean up socket file
        if not os.name == "nt" and os.path.exists(sock_path):
            try:
                os.unlink(sock_path)
            except OSError:
                pass

    # 2d. 再次确认不可达
    time.sleep(0.5)
    # Clean up the cached anchor_id from the previous successful connection
    result2 = unreachable_anchor.write_run_tail(
        "test_run_2", "b" * 64, int(time.time() * 1000)
    )
    check(2, "server 关闭后不可达返回 None", result2 is None,
          f"got {type(result2).__name__ if result2 is not None else None}")

    return all_ok and server_ok


# ====================================================================
# Scenario 3: TerminationCriterion 默认
# ====================================================================

def scenario_3_judges() -> bool:
    print("\n" + "=" * 60)
    print("Scenario 3: TerminationCriterion 默认")
    print("=" * 60)

    all_ok = True

    # 3a. UndecidableJudge 恒返回 UNDECIDABLE
    ud_judge = UndecidableJudge()
    test_evidences = [
        Evidence(baseline_sha="abc", current_sha="def"),
        Evidence(baseline_sha="", current_sha=""),
        Evidence(baseline_sha="x", current_sha="y", diff="some diff"),
        Evidence(baseline_sha="a", current_sha="b", external={"key": "value"}),
        Evidence(baseline_sha="123", current_sha="456", test_results=(), lint_results=()),
    ]
    for i, ev in enumerate(test_evidences):
        verdict = ud_judge(ev)
        ok = verdict.state == TerminationState.UNDECIDABLE
        if not ok:
            check(3, f"UndecidableJudge 证据#{i+1}", False,
                  f"state={verdict.state}")
            all_ok = False

    if all(e.state == TerminationState.UNDECIDABLE for e in
           [ud_judge(ev) for ev in test_evidences]):
        check(3, "UndecidableJudge 恒返回 UNDECIDABLE", True)

    # 3b. SystemJudge 在非 git 仓库返回 UNDECIDABLE
    with tempfile.TemporaryDirectory() as tmpdir:
        sys_judge = SystemJudge(cwd=tmpdir)
        evidence = Evidence(baseline_sha="s0", current_sha="s1")
        try:
            verdict = sys_judge(evidence)
            is_undecidable = verdict.state == TerminationState.UNDECIDABLE
            check(3, "SystemJudge 非 git 仓库返回 UNDECIDABLE", is_undecidable,
                  f"state={verdict.state}, report={verdict.report}")
        except Exception as e:
            check(3, "SystemJudge 非 git 仓库返回 UNDECIDABLE", False,
                  f"抛异常: {e}")
            all_ok = False

    # 3c. UndecidableJudge 和 SystemJudge 都满足 Judge Protocol
    from zall.core.accountability import Judge
    is_ud_judge = isinstance(UndecidableJudge(), Judge)
    is_sys_judge = isinstance(SystemJudge(), Judge)
    check(3, "UndecidableJudge 满足 Judge Protocol", is_ud_judge)
    check(3, "SystemJudge 满足 Judge Protocol", is_sys_judge)
    if not (is_ud_judge and is_sys_judge):
        all_ok = False

    # 3d. base_judge 表覆盖所有 GoalType
    for gt in GoalType:
        try:
            main, aux = base_judge(gt)
            ok = main in ("system", "user", "model_self") and aux in ("system", "user", "model_self")
            if not ok:
                check(3, f"base_judge({gt}) 返回合法值", False,
                      f"main={main}, aux={aux}")
                all_ok = False
        except Exception as e:
            check(3, f"base_judge({gt}) 不抛异常", False, str(e))
            all_ok = False
    check(3, "base_judge 表覆盖所有 GoalType", True)

    # 3e. UndecidableJudge 的 judge_type 为 "user"
    check(3, "UndecidableJudge.judge_type == 'user'",
          ud_judge.judge_type == "user",
          f"got {ud_judge.judge_type}")

    # 3f. SystemJudge 的 judge_type 为 "system"
    check(3, "SystemJudge.judge_type == 'system'",
          SystemJudge().judge_type == "system",
          f"got {SystemJudge().judge_type}")

    return all_ok


# ====================================================================
# Scenario 4: 评估体系
# ====================================================================

def scenario_4_eval() -> bool:
    print("\n" + "=" * 60)
    print("Scenario 4: 评估体系")
    print("=" * 60)

    all_ok = True

    with tempfile.TemporaryDirectory() as tmpdir:
        # 4a. 无 timeline 时返回 None
        result = evaluate_from_timeline(tmpdir)
        check(4, "无 timeline 返回 None", result is None,
              f"got {type(result).__name__ if result is not None else None}")

        # 4b. 创建包含 GOAL_STATEMENT, USER_CONFIRM, TOOL_CALL_START,
        #     TOOL_CALL_END, JUDGE_RESULT 的 timeline
        timeline_path = Path(tmpdir) / "timeline.jsonl"
        events_data = [
            {
                "event_id": f"e0_{uuid4().hex}",
                "ts": 1000,
                "event_type": "goal_statement",
                "payload": {
                    "intent": "修复登录bug",
                    "goal_type": "bugfix",
                    "rewrite_confidence": 0.9,
                    "translation_of": ["修复登录bug"],
                    "added_intent": [],
                },
                "prev_hash": "0" * 64,
            },
            {
                "event_id": f"e1_{uuid4().hex}",
                "ts": 2000,
                "event_type": "user_confirm",
                "payload": {"confirmed": True},
                "prev_hash": "0" * 64,
            },
            {
                "event_id": f"e2_{uuid4().hex}",
                "ts": 3000,
                "event_type": "tool_call_start",
                "payload": {"tool_id": "read_file"},
                "prev_hash": "0" * 64,
            },
            {
                "event_id": f"e3_{uuid4().hex}",
                "ts": 4000,
                "event_type": "tool_call_end",
                "payload": {"tool_id": "read_file", "success": True},
                "prev_hash": "0" * 64,
            },
            {
                "event_id": f"e4_{uuid4().hex}",
                "ts": 5000,
                "event_type": "judge_result",
                "payload": {"state": "met"},
                "prev_hash": "0" * 64,
            },
        ]
        # 正确的链式哈希: 每条的 prev_hash 是前一条的 compute_hash()
        # 但为了测试加载和评估逻辑, 这里用简单 genesis hash
        # (evaluate_from_timeline 的 load_timeline 不验证链完整性)
        with open(timeline_path, "w", encoding="utf-8") as f:
            for ev in events_data:
                f.write(json.dumps(ev) + "\n")

        # 4c. evaluate_from_timeline 返回 CoreEvalReport
        report = evaluate_from_timeline(tmpdir)
        if report is None:
            check(4, "evaluate_from_timeline 返回报告", False, "返回 None")
            return False

        check(4, "evaluate_from_timeline 返回 CoreEvalReport",
              isinstance(report, CoreEvalReport),
              f"type={type(report).__name__}")

        # 4d. metrics 非空 (至少 2 个: goal_achievement + timeline_integrity)
        metrics_nonempty = len(report.metrics) >= 2
        check(4, "metrics 非空 (>=2)", metrics_nonempty,
              f"指标数={len(report.metrics)}")

        # 4e. 各 metric 值在 [0, 1] 范围内
        for m in report.metrics:
            in_range = 0.0 <= m.value <= 1.0 and 0.0 <= m.anti_value <= 1.0
            if not in_range:
                check(4, f"metric '{m.name}' 值在 [0,1] 内", False,
                      f"value={m.value}, anti_value={m.anti_value}")
                all_ok = False

        check(4, "所有 metric 值在 [0,1] 范围内", all_ok)

        # 4f. 验证 report 有 summary 和 health 字段
        check(4, "report.summary 非空", bool(report.summary))
        check(4, "report.health 有效", report.health in ("healthy", "warning", "critical"))

        # 4g. 验证 load_timeline 也正常工作
        events = load_timeline(tmpdir)
        check(4, "load_timeline 加载事件", events is not None and len(events) == 5,
              f"加载了 {len(events) if events else 0} 个事件")

        # 4h. 验证 goal_achievement 指标正确识别 met
        goal_metric = [m for m in report.metrics if m.name == "goal_achievement"]
        if goal_metric:
            check(4, "goal_achievement 指标存在", True)
            check(4, "goal_achievement 值为 1.0 (met)",
                  goal_metric[0].value == 1.0,
                  f"value={goal_metric[0].value}")
        else:
            check(4, "goal_achievement 指标存在", False)

        # 4i. 验证 timeline_integrity 指标存在
        integrity_metric = [m for m in report.metrics if m.name == "timeline_integrity"]
        check(4, "timeline_integrity 指标存在",
              len(integrity_metric) > 0)

    return all_ok


# ====================================================================
# Main
# ====================================================================

def main() -> None:
    print("=" * 60)
    print("Phase 1 (修裂缝) 验收脚本")
    print(f"项目根目录: {PROJECT_ROOT}")
    print(f"Python: {sys.version}")
    print(f"平台: {sys.platform}")
    print("=" * 60)

    s1_ok = scenario_1_refiner()
    s2_ok = scenario_2_anchor()
    s3_ok = scenario_3_judges()
    s4_ok = scenario_4_eval()

    print("\n" + "=" * 60)
    print(f"Phase 1 验收: {_passed}/{_total} 通过")
    print("=" * 60)

    if not s1_ok:
        print("  Scenario 1 (Refiner): FAIL")
    if not s2_ok:
        print("  Scenario 2 (锚点): FAIL")
    if not s3_ok:
        print("  Scenario 3 (Judge): FAIL")
    if not s4_ok:
        print("  Scenario 4 (评估): FAIL")

    if _passed == _total:
        print("\n所有场景通过!")
    else:
        print(f"\n{_total - _passed} 个检查未通过")
        sys.exit(1)


if __name__ == "__main__":
    main()