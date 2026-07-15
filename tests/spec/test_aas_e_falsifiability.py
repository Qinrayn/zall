"""AAS §E 自证false条款的机械可检测 invariant test。

IPR-0 反飞天雷: 这里所有 invariant 必须 *反例可塌*。
- 反例: 构造一个违规 timeline / RunEgress, 检测器 *必须* 报出对应违规。
  若某检测器对违规沉默 → §E.2 自杀条款触发, AAS 该版塌。
- 正向: 构造一个合规 timeline, 检测器必须 return () (不误报)。

对应 docs/spec/AGENT_ALIGNMENT_SPEC.md:
  §E.2  → test_B1_2_* / test_B3_2_*
  §E.1  → test_E1_dimension_collapse (本规范不测本体塌缩, 该条由红蓝对抗
          在 DESIGN.md §1.3 已闭合; 规范层只能 *声明* 不可塌, 测试侧此条
          降为 known_open 文档化)
  §E.3  → test_E3_metric_no_reverse_index_needs_pair
  §E.4  → test_E4_level_boundary_ambiguity
  §E.5  → test_E5_upstream_settled_dependency

§E.1 与 §E.5 因属"上溯到 DESIGN.md 的本体论裁决"不属机械检测范畴, 故在
本测试中只占位标 OPEN, 由后续 DESIGN 上游决定。这本身是 PR-0 落地: 凡不可
机械检测者, 不在 mechanical invariant 测试里假装 mechanical。
"""
from __future__ import annotations

import pytest

from tests.spec.aas_minimal_schema import (
    GateDecisionEvent,
    JudgeResultEvent,
    RunEgress,
    TimelineEvent,
    aas_core_detector,
)

# ──────────────────────────────────────────────────────────────────────────
# helpers
# ──────────────────────────────────────────────────────────────────────────


def _ev(
    eid: str,
    prev: str,
    kind: str = "judge_result",
    jr: JudgeResultEvent | None = None,
    *,
    action_id: str | None = None,
    goal_intent: str | None = None,
    gate: GateDecisionEvent | None = None,
    confirms_event_id: str | None = None,
) -> TimelineEvent:
    return TimelineEvent(
        event_id=eid,
        prev_hash=prev,
        payload_kind=kind,  # type: ignore[arg-type]
        judge_result=jr,
        action_id=action_id,
        goal_intent=goal_intent,
        gate=gate,
        confirms_event_id=confirms_event_id,
    )


def _main(state: str) -> JudgeResultEvent:
    return JudgeResultEvent(termination=state, judge_subject="system", is_aux=False)  # type: ignore[arg-type]


def _aux(state: str, subj: str = "model_self") -> JudgeResultEvent:
    return JudgeResultEvent(termination=state, judge_subject=subj, is_aux=True)  # type: ignore[arg-type]


def _gate_allow(action: str, rule: str = "explicit_whitelist") -> GateDecisionEvent:
    return GateDecisionEvent(action_id=action, decision="allow", matched_rule_kind=rule)  # type: ignore[arg-type]


def _gate_deny(action: str) -> GateDecisionEvent:
    return GateDecisionEvent(action_id=action, decision="deny", matched_rule_kind="explicit_blacklist")  # type: ignore[arg-type]


def _gate_greylist(action: str, rule: str = "explicit_greylist") -> GateDecisionEvent:
    return GateDecisionEvent(action_id=action, decision="greylist", matched_rule_kind=rule)  # type: ignore[arg-type]


# ──────────────────────────────────────────────────────────────────────────
# §B1.2 核心禁令机械可检测 — claim=met 但 judge 给 undecidable/not_met
# ──────────────────────────────────────────────────────────────────────────


class TestB12ClaimMetFalsifiable:
    """§B1.2: final_claim=met 但 main Judge 实际给 undecidable/not_met → 违规."""

    def test_negative_claim_met_but_judge_undecidable(self) -> None:
        """counterexample: agent 自报 met 而真实 judge 为 undecidable → 必被检。

        如果检测器对此沉默, AAS §E.2 自杀条款触发 (B1.2 不可机械检测 = 规范自塌)。
        """
        egress = RunEgress(
            run_id="r1",
            final_run_claim="met",
            timeline=(_ev("e1", "GENESIS", jr=_main("undecidable")),),
        )
        violations = aas_core_detector()(egress)
        assert any(v.startswith("B1.2_claim_met_but_judge_undecidable") for v in violations)

    def test_negative_claim_met_but_judge_not_met(self) -> None:
        """counterexample: 假阳性 met + 真实 not_met → 必被检 (hallucination的机械化身)."""
        egress = RunEgress(
            run_id="r2",
            final_run_claim="met",
            timeline=(_ev("e1", "GENESIS", jr=_main("not_met")),),
        )
        violations = aas_core_detector()(egress)
        assert any(v.startswith("B1.2_claim_met_but_judge_not_met") for v in violations)

    def test_positive_claim_matches_met(self) -> None:
        """正向: claim=met 与 judge=met → 不误报."""
        egress = RunEgress(
            run_id="r3",
            final_run_claim="met",
            timeline=(_ev("e1", "GENESIS", jr=_main("met")),),
        )
        assert aas_core_detector()(egress) == ()

    def test_positive_claim_with_caveat_throws_no_b12(self) -> None:
        """正向: claim=met_with_caveat 配 judge=undecidable 不违反 B1.2.

        §B3.2 的 met_with_caveat 是 *显式承认* 未完全达成, 故与 §B1.2 的
        "假装 met" 不属同一违规; 不在 B1.2 检测中触发误报。
        本测验证规范不在此过度收紧 (避免把诚实降级误杀为幻觉).
        """
        egress = RunEgress(
            run_id="r4",
            final_run_claim="met_with_caveat",
            timeline=(_ev("e1", "GENESIS", jr=_main("undecidable")),),
        )
        v = aas_core_detector()(egress)
        assert not any("B1.2" in x for x in v)


# ──────────────────────────────────────────────────────────────────────────
# §B3.2 越级禁止机械可检测
# ──────────────────────────────────────────────────────────────────────────


class TestB32AuxEscalationFalsifiable:
    """§B3.2: aux 不可在 main=undecidable 时越级改 met."""

    def test_negative_aux_met_after_main_undecidable(self) -> None:
        """counterexample: main=undecidable 后 aux=met → 必被检.

        若该反例不被检出, AAS §E.2 触发 — B3.2 越级禁止不可机械检测 = 规范自塌.
        """
        egress = RunEgress(
            run_id="r5",
            final_run_claim="met",  # 注意 claim 也错了, 但本测聚焦越级那条
            timeline=(
                _ev("e1", "GENESIS", jr=_main("undecidable")),
                _ev("e2", "h(e1)", jr=_aux("met")),
            ),
        )
        v = aas_core_detector()(egress)
        assert any(x.startswith("B3.2_aux_escalation_after_main_undecidable:e2") for x in v)

    def test_negative_aux_not_met_after_main_undecidable_is_ok(self) -> None:
        """正向边界: aux 给 not_met 或 undecidable 不算越级 (越级只禁 *met*).

        防规范静默地从 "禁越级 met" 扩到 "禁 aux 任何产出", 那是过度收紧.
        """
        egress = RunEgress(
            run_id="r6",
            final_run_claim="undecidable",
            timeline=(
                _ev("e1", "GENESIS", jr=_main("undecidable")),
                _ev("e2", "h(e1)", jr=_aux("not_met")),
            ),
        )
        v = aas_core_detector()(egress)
        assert not any(x.startswith("B3.2_aux_escalation") for x in v)
        # 但 B1.2 不应触发 (claim=undecidable 也是诚实的)
        assert not any(x.startswith("B1.2") for x in v)

    def test_negative_aux_met_before_main_undecidable_is_ok(self) -> None:
        """正向边界: aux 先给 met, 主后 judge 给 met → 不算越级.

        检测器不可单看 "aux 给 met" 就报违规; 必须是 *在 main 已 undecidable 之后*.
        """
        egress = RunEgress(
            run_id="r7",
            final_run_claim="met",
            timeline=(
                _ev("e1", "GENESIS", jr=_aux("met")),
                _ev("e2", "h(e1)", jr=_main("met")),
            ),
        )
        assert aas_core_detector()(egress) == ()

    def test_main_met_resets_aux_window(self) -> None:
        """正向边界: main 先 undecidable, 再 met, 然后 aux 给 met → 不算越级.

        main 给 met 后, 此前 main=undecidable 的观察窗应被关闭.
        这条对应 _aux_terminations_after_main_undecidable 的"main 出 met/not_met
        重置观察窗"语义, 确保检测器认识 Judge 可重判.
        """
        egress = RunEgress(
            run_id="r8",
            final_run_claim="met",
            timeline=(
                _ev("e1", "GENESIS", jr=_main("undecidable")),
                _ev("e2", "h(e1)", jr=_main("met")),
                _ev("e3", "h(e2)", jr=_aux("met")),
            ),
        )
        assert aas_core_detector()(egress) == ()


# ──────────────────────────────────────────────────────────────────────────
# §B3.1 Judge 主体未声明 / 缺失 = 机械可检测违规
# ──────────────────────────────────────────────────────────────────────────


class TestB31NoMainJudgeFalsifiable:
    """§B3.1: timeline 完全无 main judge_result event → 违规 (机械可检测)."""

    def test_negative_no_judge_event_at_all(self) -> None:
        """counterexample: timeline 全无 judge_result → B3.1 报 'no_main_judge_event'.

        一个声称完成的 run 连一次 main Judge 都没跑过 = 主体缺位 = 主体未声明.
        若此不被检则 §B3.1 退出机械检测范畴 → §E.2 触发.
        """
        egress = RunEgress(
            run_id="r9",
            final_run_claim="met",
            timeline=(
                _ev("e1", "GENESIS", kind="tool_call_end"),
                _ev("e2", "h(e1)", kind="model_call"),
            ),
        )
        v = aas_core_detector()(egress)
        assert "B3.1_no_main_judge_event_in_timeline" in v

    def test_negative_only_aux_no_main(self) -> None:
        """counterexample: 只有 aux judgment无 main → 同样 B3.1 违规 (aux 不可代替 main)."""
        egress = RunEgress(
            run_id="r10",
            final_run_claim="met",
            timeline=(_ev("e1", "GENESIS", jr=_aux("met")),),
        )
        v = aas_core_detector()(egress)
        assert "B3.1_no_main_judge_event_in_timeline" in v


# ──────────────────────────────────────────────────────────────────────────
# §B4.1 timeline 链式 prev_hash 形态断裂 — 机械可检测
# ──────────────────────────────────────────────────────────────────────────


class TestB41ChainBrokenFalsifiable:
    """§B4.1: append-only timeline must链式 prev_hash; 形态断裂即违规.

    机械可测的断裂 (规范层承诺): 首条 prev_hash != GENESIS / 任意 prev_hash 空.
    机械 *不可* 测的断裂 (故意不测, OPEN): prev_hash 与前一 event 实际哈希
    不一致 — 这需规范规定哈希函数, AAS v0.1 不规定. 若假装能测则违 §E.2.
    """

    def test_negative_first_event_not_genesis(self) -> None:
        """counterexample: 首条 event prev_hash != GENESIS → 链形态断裂 → 必被检."""
        egress = RunEgress(
            run_id="r11",
            final_run_claim="met",
            timeline=(
                TimelineEvent(
                    event_id="e1",
                    prev_hash="not_genesis",  # 形态断裂
                    payload_kind="judge_result",
                    judge_result=_main("met"),
                ),
            ),
        )
        v = aas_core_detector()(egress)
        assert any(x.startswith("B4.1_timeline_chain_broken:e1@0") for x in v)

    def test_negative_empty_prev_hash(self) -> None:
        """counterexample: 非首条 event prev_hash 空 → 链断裂 → 必被检."""
        egress = RunEgress(
            run_id="r12",
            final_run_claim="met",
            timeline=(
                _ev("e1", "GENESIS", jr=_main("met")),
                TimelineEvent(
                    event_id="e2",
                    prev_hash="",  # 空即断裂
                    payload_kind="tool_call_end",
                ),
            ),
        )
        v = aas_core_detector()(egress)
        assert any(x.startswith("B4.1_timeline_chain_broken:e2@1") for x in v)

    def test_positive_genesis_chain_ok(self) -> None:
        """正向: 首条 GENESIS + 其余非空 → 不报 B4.1."""
        egress = RunEgress(
            run_id="r13",
            final_run_claim="met",
            timeline=(
                _ev("e1", "GENESIS", jr=_main("met")),
                _ev("e2", "h(e1)", kind="tool_call_end"),
            ),
        )
        v = aas_core_detector()(egress)
        assert not any(x.startswith("B4.1") for x in v)


# ──────────────────────────────────────────────────────────────────────────
# §B4.3 不reproducible run 被静默计入reproducible分母 — 机械可检测
# ──────────────────────────────────────────────────────────────────────────


class TestB43NonReproducibleSilentFalsifiable:
    """§B4.3: 落入 non_reproducible_by_construction 的 run must显式标 cause.

    静默 (cause=None) 即把不可复现 run 偷偷计入"已复现"分母, 美化复现率,
    属 Goodhart 通道 (AAS §F 静默即假象). 机械可测的子集:
      timeline 断链 (B4.1 已检出) && RunEgress.non_reproducible_cause is None
      → B4.3 违规.

    其他 non_reproducible 类型 (anchor_unreachable / data_snapshot 缺) 不在
    规范层机械检测能力内 — 它们属运行时事实, 规范层 timeline 看不见.
    """

    def test_negative_chain_broken_but_silent(self) -> None:
        """counterexample: timeline 断链 + cause=None → B4.3 静默违规 → 必被检."""
        egress = RunEgress(
            run_id="r14",
            final_run_claim="met",
            non_reproducible_cause=None,  # 静默!
            timeline=(
                TimelineEvent(
                    event_id="e1",
                    prev_hash="not_genesis",  # 断链
                    payload_kind="judge_result",
                    judge_result=_main("met"),
                ),
            ),
        )
        v = aas_core_detector()(egress)
        assert "B4.3_non_reproducible_silently_omitted" in v

    def test_positive_chain_broken_with_cause_disclosed(self) -> None:
        """正向: timeline 断链 + cause 显式标注 → 不报 B4.3 (诚实).

        Agent 显式承认 run 不可复现, 即不被 Goodhart 诱惑; 规范不在此再罚.
        (B4.1 仍会报链断裂, 但 B4.3 静默那条不触发 — 两违规独立.)
        """
        egress = RunEgress(
            run_id="r15",
            final_run_claim="met",
            non_reproducible_cause="timeline_chain_broken_at_construction",
            timeline=(
                TimelineEvent(
                    event_id="e1",
                    prev_hash="not_genesis",
                    payload_kind="judge_result",
                    judge_result=_main("met"),
                ),
            ),
        )
        v = aas_core_detector()(egress)
        assert "B4.3_non_reproducible_silently_omitted" not in v
        # 但 B4.1 仍报 (链断了就是断了, 显式标 cause 不豁免 B4.1)
        assert any(x.startswith("B4.1") for x in v)

    def test_positive_clean_run_no_b43(self) -> None:
        """正向: 干净 timeline + cause=None → 不报 B4.3 (无reproducible负担)."""
        egress = RunEgress(
            run_id="r16",
            final_run_claim="met",
            non_reproducible_cause=None,
            timeline=(_ev("e1", "GENESIS", jr=_main("met")),),
        )
        v = aas_core_detector()(egress)
        assert not any(x.startswith("B4.3") for x in v)


# ──────────────────────────────────────────────────────────────────────────
# §B1.3 Goal lock后 run 内不可变 — 机械可检测
# ──────────────────────────────────────────────────────────────────────────


class TestB13GoalMutationFalsifiable:
    """§B1.3: Goal 在用户confirm后 run 内不可变 (AAS §B1.3 / §E.2)."""

    def test_negative_goal_intent_changed(self) -> None:
        """counterexample: 首条 goal_statement intent=A, 后续 intent=B → 必报 B1.3.

        目标漂移是 DESIGN.md §3.1 点名的顽疾; 若检测器对此沉默,
        §B1.3 不可机械检测 → §E.2 触发.
        """
        egress = RunEgress(
            run_id="r17",
            final_run_claim="met",
            timeline=(
                _ev("e1", "GENESIS", kind="goal_statement", goal_intent="fix bug #42"),
                _ev("e2", "h(e1)", kind="goal_statement", goal_intent="refactor module"),
                _ev("e3", "h(e2)", jr=_main("met")),
            ),
        )
        v = aas_core_detector()(egress)
        assert any(x.startswith("B1.3_goal_mutated_after_lock:e2") for x in v)

    def test_positive_single_goal_statement(self) -> None:
        """正向: 单条 goal_statement → 不报 B1.3."""
        egress = RunEgress(
            run_id="r18",
            final_run_claim="met",
            timeline=(
                _ev("e1", "GENESIS", kind="goal_statement", goal_intent="fix bug #42"),
                _ev("e2", "h(e1)", jr=_main("met")),
            ),
        )
        v = aas_core_detector()(egress)
        assert not any(x.startswith("B1.3") for x in v)

    def test_positive_repeated_same_intent(self) -> None:
        """正向: 多条 goal_statement 但 intent 一致 → 不报 (重复声明 ≠ 漂移)."""
        egress = RunEgress(
            run_id="r19",
            final_run_claim="met",
            timeline=(
                _ev("e1", "GENESIS", kind="goal_statement", goal_intent="fix bug #42"),
                _ev("e2", "h(e1)", kind="goal_statement", goal_intent="fix bug #42"),
                _ev("e3", "h(e2)", jr=_main("met")),
            ),
        )
        v = aas_core_detector()(egress)
        assert not any(x.startswith("B1.3") for x in v)

    def test_positive_no_goal_statement_no_b13(self) -> None:
        """正向边界: 无 goal_statement → 不报 B1.3 (B1.3 是不可变性, 非存在性).

        存在性属 §B1.1, 不在 §E.2 自杀条款内, 故本检测器不测 —
        不假装闭合 §B1.1 的机械检测 (守 §E.2 精神).
        """
        egress = RunEgress(
            run_id="r20",
            final_run_claim="met",
            timeline=(_ev("e1", "GENESIS", jr=_main("met")),),
        )
        v = aas_core_detector()(egress)
        assert not any(x.startswith("B1.3") for x in v)


# ──────────────────────────────────────────────────────────────────────────
# §B2.1 deny-by-default — 机械可检测
# ──────────────────────────────────────────────────────────────────────────


class TestB21DenyByDefaultFalsifiable:
    """§B2.1: 无rule匹配时default应归 greylist, 不可default allow."""

    def test_negative_unmatched_default_allow(self) -> None:
        """counterexample: gate_decision(allow, unmatched_default) → 必报 B2.1.

        无匹配规则时 agent 默认 allow 即越界默认, 违反 deny-by-default.
        """
        egress = RunEgress(
            run_id="r21",
            final_run_claim="met",
            timeline=(
                _ev("e1", "GENESIS", kind="gate_decision",
                    action_id="rm -rf /", gate=_gate_allow("rm -rf /", "unmatched_default")),
                _ev("e2", "h(e1)", jr=_main("met")),
            ),
        )
        v = aas_core_detector()(egress)
        assert any(x.startswith("B2.1_deny_by_default_violated:e1") for x in v)

    def test_positive_explicit_whitelist_allow(self) -> None:
        """正向: gate_decision(allow, explicit_whitelist) → 不报 (命中已声明rule)."""
        egress = RunEgress(
            run_id="r22",
            final_run_claim="met",
            timeline=(
                _ev("e1", "GENESIS", kind="gate_decision",
                    action_id="read_file", gate=_gate_allow("read_file", "explicit_whitelist")),
                _ev("e2", "h(e1)", jr=_main("met")),
            ),
        )
        v = aas_core_detector()(egress)
        assert not any(x.startswith("B2.1") for x in v)

    def test_positive_unmatched_defaults_to_greylist(self) -> None:
        """正向: gate_decision(greylist, unmatched_default) → 不报 (正确default)."""
        egress = RunEgress(
            run_id="r23",
            final_run_claim="met",
            timeline=(
                _ev("e1", "GENESIS", kind="gate_decision",
                    action_id="unknown_cmd", gate=_gate_greylist("unknown_cmd", "unmatched_default")),
                _ev("e2", "h(e1)", jr=_main("met")),
            ),
        )
        v = aas_core_detector()(egress)
        assert not any(x.startswith("B2.1") for x in v)

    def test_positive_deny_does_not_trigger_b21(self) -> None:
        """正向边界: gate_decision(deny, explicit_blacklist) → 不报 B2.1 (deny 不是 allow)."""
        egress = RunEgress(
            run_id="r24",
            final_run_claim="met",
            timeline=(
                _ev("e1", "GENESIS", kind="gate_decision",
                    action_id="rm -rf /", gate=_gate_deny("rm -rf /")),
                _ev("e2", "h(e1)", jr=_main("met")),
            ),
        )
        v = aas_core_detector()(egress)
        assert not any(x.startswith("B2.1") for x in v)


# ──────────────────────────────────────────────────────────────────────────
# §B2.2 blacklist 不可直接execute — 机械可检测
# ──────────────────────────────────────────────────────────────────────────


class TestB22BlacklistDirectExecFalsifiable:
    """§B2.2: blacklist action不可直接execute, must经等价replace或 Override."""

    def test_negative_deny_then_direct_execute(self) -> None:
        """counterexample: gate_decision(deny, A) → tool_call_end(A) 无 override → 必报 B2.2."""
        egress = RunEgress(
            run_id="r25",
            final_run_claim="met",
            timeline=(
                _ev("e1", "GENESIS", kind="gate_decision",
                    action_id="git_push_force", gate=_gate_deny("git_push_force")),
                _ev("e2", "h(e1)", kind="tool_call_end", action_id="git_push_force"),
                _ev("e3", "h(e2)", jr=_main("met")),
            ),
        )
        v = aas_core_detector()(egress)
        assert any(x.startswith("B2.2_blacklist_directly_executed:git_push_force") for x in v)

    def test_positive_deny_then_override_then_execute(self) -> None:
        """正向: gate_decision(deny, A) → override(A) → tool_call_end(A) → 不报.

        Override 是 §B2.2 的合法出路: user 显式批准 + 审计.
        """
        egress = RunEgress(
            run_id="r26",
            final_run_claim="met",
            timeline=(
                _ev("e1", "GENESIS", kind="gate_decision",
                    action_id="git_push_force", gate=_gate_deny("git_push_force")),
                _ev("e2", "h(e1)", kind="override", action_id="git_push_force"),
                _ev("e3", "h(e2)", kind="tool_call_end", action_id="git_push_force"),
                _ev("e4", "h(e3)", jr=_main("met")),
            ),
        )
        v = aas_core_detector()(egress)
        assert not any(x.startswith("B2.2") for x in v)

    def test_positive_deny_then_equivalent_action(self) -> None:
        """正向: gate_decision(deny, A) → tool_call_end(B) B≠A → 不报.

        Agent 改用等价替换 (不同 action_id) 不算直接执行 A.
        等价替换的真伪判定属 §F OPEN, 本检测器只测 action_id 一致性.
        """
        egress = RunEgress(
            run_id="r27",
            final_run_claim="met",
            timeline=(
                _ev("e1", "GENESIS", kind="gate_decision",
                    action_id="git_push_force", gate=_gate_deny("git_push_force")),
                _ev("e2", "h(e1)", kind="gate_decision",
                    action_id="git_push_force_with_lease",
                    gate=_gate_allow("git_push_force_with_lease", "explicit_whitelist")),
                _ev("e3", "h(e2)", kind="tool_call_end", action_id="git_push_force_with_lease"),
                _ev("e4", "h(e3)", jr=_main("met")),
            ),
        )
        v = aas_core_detector()(egress)
        assert not any(x.startswith("B2.2") for x in v)

    def test_positive_allow_then_execute_no_b22(self) -> None:
        """正向边界: gate_decision(allow, A) → tool_call_end(A) → 不报 B2.2 (非 deny)."""
        egress = RunEgress(
            run_id="r28",
            final_run_claim="met",
            timeline=(
                _ev("e1", "GENESIS", kind="gate_decision",
                    action_id="read_file", gate=_gate_allow("read_file", "explicit_whitelist")),
                _ev("e2", "h(e1)", kind="tool_call_end", action_id="read_file"),
                _ev("e3", "h(e2)", jr=_main("met")),
            ),
        )
        v = aas_core_detector()(egress)
        assert not any(x.startswith("B2.2") for x in v)


# ──────────────────────────────────────────────────────────────────────────
# §9.3 "无 Goal 不调tool" — 机械可检测 (DESIGN.md §9.3 红蓝对抗产出)
# ──────────────────────────────────────────────────────────────────────────


class TestS93NoToolWithoutLockedGoal:
    """§9.3: tool_call_start 前must有已confirm goal_statement (DESIGN.md §9.3).

    红蓝对抗产出: 采纳 Claude Code 对话式 UX 时, "对话即本体" 的偷渡风险
    由这条不变量对治 — agent 可不在用户说完后立即锁定 (多轮细化 OK),
    但一旦调工具, 此刻必须已有锁定的 Goal。机械可检测, 候选加入 AAS v0.2。
    """

    def test_negative_tool_call_before_any_goal(self) -> None:
        """counterexample: tool_call_start 出现在任何 goal_statement 之前 → 必报 §9.3.

        agent 在无 Goal 下调了工具 = 偷渡回"对话即本体" (§1.1 病根).
        若检测器对此沉默, §9.3 不可机械检测 → 规范自塌.
        """
        egress = RunEgress(
            run_id="r29",
            final_run_claim="met",
            timeline=(
                _ev("e1", "GENESIS", kind="tool_call_start", action_id="grep"),
                _ev("e2", "h(e1)", kind="tool_call_end", action_id="grep"),
                _ev("e3", "h(e2)", jr=_main("met")),
            ),
        )
        v = aas_core_detector()(egress)
        assert any(x.startswith("S9.3_tool_call_without_locked_goal:e1") for x in v)

    def test_negative_goal_proposed_but_not_confirmed(self) -> None:
        """counterexample: goal_statement 存在但无 user_confirm, 就调了tool → 必报 §9.3.

        Goal 提议 ≠ Goal 锁定; 未确认就调工具 = 偷渡.
        """
        egress = RunEgress(
            run_id="r30",
            final_run_claim="met",
            timeline=(
                _ev("e1", "GENESIS", kind="goal_statement", goal_intent="fix bug"),
                # 无 user_response confirm → 未lock
                _ev("e2", "h(e1)", kind="tool_call_start", action_id="grep"),
                _ev("e3", "h(e2)", kind="tool_call_end", action_id="grep"),
                _ev("e4", "h(e3)", jr=_main("met")),
            ),
        )
        v = aas_core_detector()(egress)
        assert any(x.startswith("S9.3_tool_call_without_locked_goal:e2") for x in v)

    def test_negative_confirm_points_to_wrong_event(self) -> None:
        """counterexample: user_response 的 confirms_event_id 指向非 goal_statement → 不算lock."""
        egress = RunEgress(
            run_id="r31",
            final_run_claim="met",
            timeline=(
                _ev("e1", "GENESIS", kind="goal_statement", goal_intent="fix bug"),
                # confirms_event_id 指向自己而非 e1 → 不构成 goal confirm
                _ev("e2", "h(e1)", kind="user_response", confirms_event_id="e2"),
                _ev("e3", "h(e2)", kind="tool_call_start", action_id="grep"),
                _ev("e4", "h(e3)", kind="tool_call_end", action_id="grep"),
                _ev("e5", "h(e4)", jr=_main("met")),
            ),
        )
        v = aas_core_detector()(egress)
        assert any(x.startswith("S9.3_tool_call_without_locked_goal:e3") for x in v)

    def test_positive_goal_confirmed_then_tool_call(self) -> None:
        """正向: goal_statement → user_confirm → tool_call_start → 不报 §9.3."""
        egress = RunEgress(
            run_id="r32",
            final_run_claim="met",
            timeline=(
                _ev("e1", "GENESIS", kind="goal_statement", goal_intent="fix bug"),
                _ev("e2", "h(e1)", kind="user_response", confirms_event_id="e1"),
                _ev("e3", "h(e2)", kind="tool_call_start", action_id="grep"),
                _ev("e4", "h(e3)", kind="tool_call_end", action_id="grep"),
                _ev("e5", "h(e4)", jr=_main("met")),
            ),
        )
        v = aas_core_detector()(egress)
        assert not any(x.startswith("S9.3") for x in v)

    def test_positive_multiple_tool_calls_after_confirm(self) -> None:
        """正向: 一次confirm后多次 tool_call_start 都不报 (lock持续)."""
        egress = RunEgress(
            run_id="r33",
            final_run_claim="met",
            timeline=(
                _ev("e1", "GENESIS", kind="goal_statement", goal_intent="fix bug"),
                _ev("e2", "h(e1)", kind="user_response", confirms_event_id="e1"),
                _ev("e3", "h(e2)", kind="tool_call_start", action_id="grep"),
                _ev("e4", "h(e3)", kind="tool_call_end", action_id="grep"),
                _ev("e5", "h(e4)", kind="tool_call_start", action_id="read"),
                _ev("e6", "h(e5)", kind="tool_call_end", action_id="read"),
                _ev("e7", "h(e6)", jr=_main("met")),
            ),
        )
        v = aas_core_detector()(egress)
        assert not any(x.startswith("S9.3") for x in v)

    def test_positive_no_tool_calls_no_violation(self) -> None:
        """正向边界: run 无 tool_call_start → 不报 §9.3 (纯对话pattern)."""
        egress = RunEgress(
            run_id="r34",
            final_run_claim="undecidable",
            timeline=(
                _ev("e1", "GENESIS", kind="model_call"),
                _ev("e2", "h(e1)", jr=_main("undecidable")),
            ),
        )
        v = aas_core_detector()(egress)
        assert not any(x.startswith("S9.3") for x in v)


# ──────────────────────────────────────────────────────────────────────────
# §E.3 / §E.4 / §E.1 / §E.5 — 故意 *不* 在本file中占位
# ──────────────────────────────────────────────────────────────────────────
#
# 这四条在规范层均 *不可机械检测*, 故按 IPR-0 "占位不构成 invariant" 的纪律,
# 此处不写 test — 写一个 assert True 的占位只会让 CI 绿条假装测过,
# 反而违反 AAS §E.2 的精神 (沉默即假象, 见 §F).
#
# 各条的归属:
#   §E.1 本体塌缩 → DESIGN.md §1.3 红蓝对抗已闭合, 规范层不重复对抗本体裁决
#   §E.3 metric 反metric配对 → 参照implementation侧 tests/test_metrics_r_metric.py 守
#   §E.4 Level 边界裁决 → AAS §F OPEN, 等 v0.2 handle, 不可机械检测
#   §E.5 上游 SETTLED → 由 DESIGN.md 各节state标守, 规范层不重复守
#
# 即: 这里的 *空白* 才是合规姿态. 填一个绿勾反而是违规.
