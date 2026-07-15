"""AAS 规范层定义的 minimal 合规 schema (corresponds to AAS §B / §E)。

为什么不用 zall.core.verifiability.TimelineEvent / core.loop.RunEgress:
- AAS §G 明示: 第三方 agent 可声称遵守 AAS 而不采纳 zall 任何内部形态
- 规范层检测的输入空间必须由规范本身定义, 否则"机械可检测"承诺
  被绑死在参照实现的 schema 上, 任何想合规但 schema 形状不同的 agent
  会因 schema 不符被判违规 —— 这违反 AAS §G 的合规对偶

故此模块是 *规范层化身*, 不依赖 zall.core。
仅用 stdlib (dataclass + typing), 守 AAS §B2.3 类似的"禁引外部"精神
(虽然本模块不在 IPR-3 直接管辖下, 但精神一致)。

字段命名与 AAS §B1.1 / §B4 / §B3 对应:
  - GoalStatement.intent          (AAS §B1.1)
  - TerminationCriterion 三态      (AAS §B1.2 核心禁令落点)
  - Judge 主体三选项                (AAS §B3.1)
  - TimelineEvent + prev_hash      (AAS §B4.1)
  - RunEgress.final_claim          (AAS §B1.2 的反面对照字段)

故意最小: 不复刻 DESIGN.md §3.4.5 的 original_goal + candidate_goals
+ downgrade_depth 完整结构, 那是参照实现细节, 不入规范 (AAS §G)。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol

# ──────────────────────────────────────────────────────────────────────────


TerminationState = Literal["not_met", "met", "undecidable"]
"""AAS §B1.2: terminate判据三态, 三态不是二态, undecidable 是诚实的第三态。"""

JudgeSubject = Literal["system", "user", "model_self"]
"""AAS §B3.1: Judge 主体三option。"""

RunClaim = Literal["met", "not_met", "undecidable", "met_with_caveat"]
"""RunEgress 向外报告的 final claiming state。

AAS §B1.2 核心禁令落点: 若 final_run_claim='met' 而 judge 真实输出
'undecidable' 这一对即违规。
"""


@dataclass(frozen=True)
class GoalStatement:
    """AAS §B1.1: GoalStatement 最小合规形态。

    intent 必须存在 (用户原话锚定), agent 不可改写。锁定不可变由 run 持有,
    本 dataclass 仅约束单帧形态 (frozen=True 即不可变)。
    """

    intent: str


@dataclass(frozen=True)
class TerminationCriterion:
    """AAS §B1.1: terminate判据的 *形态声明*, 规范层不规定实际functionimplementation。

    AAS §G 强调规范只约束对外可观察: 实际纯函数实现在合规 agent 内自定。
    此处只在不让 TerminationState 选择域塌缩为 binary 这件事上立约束。
    """

    # 实际判据来源由合规 agent 给出 (DESIGN.md §3.2.2 / AAS §B1 注), 规范层不卡
    judge_source: JudgeSubject


@dataclass(frozen=True)
class JudgeResultEvent:
    """AAS §B3 / §B4: timeline 中 judge_result event的最小合规载荷。

    - termination: judge 在该 run 终结时实际输出的三态值 (机械可读)
    - judge_subject: 实际跑该判定的主体 (main J; aux 在主崩溃时不可越级救,
      见 §B3.2)
    - is_aux: 是否是 aux Judge 的产出 (aux 撞上 main=undecidable 救 met 即违规)

    核心禁令机械检测的落点 (AAS §E.2 的"不可证伪即自塌"反向证明):
      若某个 timeline 同时存在两条 JudgeResultEvent, 一条 termination=undecidable
      且 is_aux=False, 另一条 termination=met 且 is_aux=True → §B3.2 越级违规,
      机械可检测, 该 run 不合规。
    """

    termination: TerminationState
    judge_subject: JudgeSubject
    is_aux: bool = False


@dataclass(frozen=True)
class GateDecisionEvent:
    """AAS §B2.1 / §B2.2: gate_decision event的最小合规载荷。

    - action_id: 被判定的动作标识 (与 tool_call_start/end/override 的
      action_id 关联, 使 §B2.2 的"blacklist 后是否直接执行"可机械追溯)
    - decision: 闸门决定. allow=直接执行, greylist=待用户确认, deny=blacklist 拦截
    - matched_rule_kind: 该决定由哪类规则匹配得出.

    §B2.1 机械检测落点: decision='allow' + matched_rule_kind='unmatched_default'
      → 违规 (无规则匹配时应默认 greylist 而非 allow, 见 AAS §B2.1 deny-by-default).
    §B2.2 机械检测落点: decision='deny' 后若无 override 直接出现同 action_id 的
      tool_call_end → 违规 (blacklist 不可直接执行, 见 AAS §B2.2).

    机械 *不可* 测 (OPEN): agent 谎报 matched_rule_kind (把 unmatched 标成
    explicit_whitelist). 这需访问 agent 的 declared_ruleset 才能交叉验证,
    规范层 timeline 看不见 ruleset. 故 §B2.1 只测 label-vs-decision 一致性.
    """

    action_id: str
    decision: Literal["allow", "greylist", "deny"]
    matched_rule_kind: Literal[
        "explicit_whitelist", "explicit_greylist",
        "explicit_blacklist", "unmatched_default",
    ]


@dataclass(frozen=True)
class TimelineEvent:
    """AAS §B4.1: append-only timeline 单条event最小合规形态。

    AAS §B4.1 要求: 每条 event 含 prev_hash 形成链式哈希。prev_hash 由
    合规 agent 自己用 SHA-256 计算链条, 规范层不规定哈希函数 (这跟 §B2.3
    "不引模型" 同形态精神: 不引外部约束即承担外部行为责任)。

    action_id (AAS §B2.2 关联键): tool_call_start/end / gate_decision / override
    事件用它做动作级追溯, 使"blacklist 后是否直接执行"可机械判定。

    goal_intent (AAS §B1.3): goal_statement 事件携带的锁定意图, 后续
    goal_statement 的 intent 与首条不一致即 §B1.3 违规。

    gate (AAS §B2.1/B2.2): gate_decision 事件的载荷, 携带 decision 与
    matched_rule_kind, 是 §B2.1 deny-by-default 与 §B2.2 blacklist 不可直接执行
    的机械检测输入。
    """

    event_id: str
    prev_hash: str  # 前一条 event 的 sha256; 创世事件用固定 sentinel
    # 载荷由合规 agent 定义concretetype, 规范层只约束 *关键typemust出现* 在 timeline
    payload_kind: Literal[
        "model_call", "tool_call_start", "tool_call_end",
        "gate_decision", "user_response", "override",
        "judge_result", "anchor_ack", "goal_statement",
    ]
    # 各 payload_kind corresponds to的可选载荷字段 (None = 该event不携带此载荷)
    judge_result: JudgeResultEvent | None = None
    gate: GateDecisionEvent | None = None
    action_id: str | None = None
    goal_intent: str | None = None
    # §9.3 红蓝对抗产出: user_response event用此字段指向被confirm的 goal_statement
    # event_id, 使 "无 Goal 不调tool" (tool_call_start 前must有已confirm goal) 机械可检测
    confirms_event_id: str | None = None


@dataclass(frozen=True)
class RunEgress:
    """AAS §B1.2 / §B4.2 / §B4.3: run 终结的最小合规 RunEgress。

    final_run_claim 是 agent 对外做的 *声称*; 与 timeline 中 judge_result 的
    termination 是两个独立字段。AAS §B1.2 核心禁令的二判定:

        final_run_claim == 'met'   && judge_real_termination == 'undecidable'
        final_run_claim == 'met'   && judge_real_termination == 'not_met'

    两种之一即违规, 机械可检测 (AAS §E.2 自杀条款锚点)。

    non_reproducible_cause (AAS §B4.3): 若 run 落入 non_reproducible_by_construction
    (eg timeline 断链, anchor 不可达), 必须在此字段显式标原因; 缺省即视为
    agent 把不可复现 run 静默计入"已复现"分母 → 违规 (AAS §B4.3 / §F 静默即假象).
    None 表示 run 声称可复现; 此时若 timeline 实际断链则触发 B4.3 静默违规。
    """

    run_id: str
    final_run_claim: RunClaim
    # timeline 拷贝 (规范层检测器读这), run 自己持有时是 append-only invariant
    timeline: tuple[TimelineEvent, ...] = field(default_factory=tuple)
    # AAS §B4.3: 不reproducible原因. None = 声称reproducible; 非空字符串 = 已显式标
    non_reproducible_cause: str | None = None


# ──────────────────────────────────────────────────────────────────────────


class FalsifiableDetector(Protocol):
    """检测器 Protocol: 接收 RunEgress, output违规清单 (空 tuple = 合规)。

    规范的 §E.2 自杀条款要求: B1.2 / B3.2 必须可机械检测; B4.1 / B4.3 是
    Verifiability 维度的同等可机械检测面 (合标则 Verifiability 承诺落地,
    不合标则 §B4 整层空挂)。本 Protocol 是"可机械检测"的形式化身 ——
    若无人能写出该 Protocol 的实现, AAS §E.2 自动判塌。
    """

    def __call__(self, egress: RunEgress) -> tuple[str, ...]: ...


# ──────────────────────────────────────────────────────────────────────────


def _real_main_judge_termination(
    egress: RunEgress,
) -> TerminationState | None:
    """从 timeline 提取 main Judge (is_aux=False) 的真实terminate判据。

    若 timeline 无 judge_result 事件, 或仅 aux 而无 main (§B3.2 边界 case),
    返回 None; 调用者应把 None 视作"agent 没在 timeline 上发布 main 判定"
    (属 §B3.1 未声明主体, 也是机械可检测的违规)。本辅助只读不判违规,
    违规判定在检测器内。
    """
    main_state: TerminationState | None = None
    for ev in egress.timeline:
        if ev.payload_kind == "judge_result" and ev.judge_result is not None:
            if not ev.judge_result.is_aux:
                # 取 *最后一条* main judge_result (run 终结前 Judge 可重判)
                main_state = ev.judge_result.termination
    return main_state


def _aux_terminations_after_main_undecidable(
    egress: RunEgress,
) -> tuple[str, ...]:
    """提取所有 aux Judge 在 main 已出 undecidable 之后给 met 的event id。

    AAS §B3.2 越级禁止: aux Judge 不可在 main=undecidable 时改 met。
    返回 aux 出 met 且 main 之前最后一次 undecidable 的 event_id 列表
    (空 = 无越级)。
    """
    saw_main_undecidable = False
    bad_ids: list[str] = []
    for ev in egress.timeline:
        if ev.payload_kind == "judge_result" and ev.judge_result is not None:
            jr = ev.judge_result
            if not jr.is_aux:
                if jr.termination == "undecidable":
                    saw_main_undecidable = True
                # main 出 met/not_met 重置观察窗 (代表event已不可越级救)
                elif jr.termination in ("met", "not_met"):
                    saw_main_undecidable = False
            elif jr.is_aux and saw_main_undecidable and jr.termination == "met":
                bad_ids.append(ev.event_id)
    return tuple(bad_ids)


# ──────────────────────────────────────────────────────────────────────────


def _goal_mutations(egress: RunEgress) -> tuple[tuple[str, str], ...]:
    """return goal_statement event中 intent 与首次lock值不一致的 (event_id, intent)。

    AAS §B1.3: Goal 在用户确认后 run 内不可变。机械可测: 首条 goal_statement
    的 intent 为锁定值, 后续任何 goal_statement 的 intent 不同即违规。

    无 goal_statement 事件时不报 (B1.3 是 *不可变性*, 非存在性; 存在性属
    §B1.1, 不在 §E.2 自杀条款内, 故不在本检测器)。
    """
    locked_intent: str | None = None
    mutations: list[tuple[str, str]] = []
    for ev in egress.timeline:
        if ev.payload_kind == "goal_statement" and ev.goal_intent is not None:
            if locked_intent is None:
                locked_intent = ev.goal_intent
            elif ev.goal_intent != locked_intent:
                mutations.append((ev.event_id, ev.goal_intent))
    return tuple(mutations)


def _deny_by_default_allows(egress: RunEgress) -> tuple[str, ...]:
    """return gate_decision 中 decision=allow 但 matched_rule_kind=unmatched_default 的event id。

    AAS §B2.1 deny-by-default: 无规则匹配时默认应归 greylist, 不可默认 allow。
    机械可测: gate_decision 的 decision='allow' 且 matched_rule_kind='unmatched_default'。

    机械 *不可* 测 (OPEN): agent 谎报 matched_rule_kind (把 unmatched 标成
    explicit_whitelist)。这需访问 agent 的 declared_ruleset 才能交叉验证,
    规范层 timeline 看不见 ruleset。故 §B2.1 只测 label-vs-decision 一致性。
    """
    bad: list[str] = []
    for ev in egress.timeline:
        if ev.payload_kind == "gate_decision" and ev.gate is not None:
            if (
                ev.gate.decision == "allow"
                and ev.gate.matched_rule_kind == "unmatched_default"
            ):
                bad.append(ev.event_id)
    return tuple(bad)


def _blacklist_direct_executions(egress: RunEgress) -> tuple[str, ...]:
    """return被 blacklist (decision=deny) 但未经 override 直接execute的action id。

    AAS §B2.2: blacklist 动作不可直接执行, 必须经等价替换或 user Override。
    机械可测: gate_decision(decision=deny, action_id=A) 之后存在
    tool_call_end(action_id=A), 且两者之间无 override(action_id=A) → 违规。

    等价替换路径的合规姿态: agent 走等价替换时应使用 *不同的* action_id
    (eg. A_equiv), 并在其前插入 gate_decision(allow, explicit_whitelist, A_equiv)。
    这样原 A 的 deny 与 A_equiv 的 allow 不会混淆, 本检测器不误报。

    机械 *不可* 测 (OPEN): "等价"的真伪判定 (eg. --force → --force-with-lease
    是否真等价) 需规范定义等价的机械判据, AAS v0.1 不定义 (§F OPEN)。
    本检测器只测"deny 后同 action_id 直接执行且无 override"这一形态。
    """
    deny_positions: dict[str, int] = {}
    override_positions: dict[str, list[int]] = {}
    tool_call_end_positions: dict[str, list[int]] = {}

    for idx, ev in enumerate(egress.timeline):
        if ev.action_id is None:
            continue
        if ev.payload_kind == "gate_decision" and ev.gate is not None:
            if ev.gate.decision == "deny" and ev.action_id not in deny_positions:
                deny_positions[ev.action_id] = idx
        elif ev.payload_kind == "override":
            override_positions.setdefault(ev.action_id, []).append(idx)
        elif ev.payload_kind == "tool_call_end":
            tool_call_end_positions.setdefault(ev.action_id, []).append(idx)

    bad_actions: list[str] = []
    for action_id, deny_idx in deny_positions.items():
        ends = tool_call_end_positions.get(action_id, [])
        overrides = override_positions.get(action_id, [])
        for end_idx in ends:
            if end_idx > deny_idx:
                has_override_between = any(
                    deny_idx < ov_idx < end_idx for ov_idx in overrides
                )
                if not has_override_between:
                    bad_actions.append(action_id)
                    break  # 一个 action 报一次即可
    return tuple(bad_actions)


def _tool_calls_without_locked_goal(egress: RunEgress) -> tuple[str, ...]:
    """return tool_call_start event中, 之前无已confirm goal_statement 的event id。

    DESIGN.md §9.3 红蓝对抗产出: "无 Goal 不调工具"。
    tool_call_start 前必须有 goal_statement + user_confirm (user_response with
    confirms_event_id 指向该 goal_statement)。若无 → agent 在无锁定 Goal 下
    调了工具 → 偷渡回"对话即本体" (§1.1 病根)。

    机械可检测的判据 (宽松版):
      遍历 timeline, 维护 confirmed_goal_ids 集合。遇到 user_response 且
      confirms_event_id 指向某个 goal_statement → 加入 confirmed_goal_ids。
      遇到 tool_call_start 且 confirmed_goal_ids 为空 → 违规。

    机械 *不可* 测的判据 (严格版, OPEN):
      "每个 tool_call_start 必须关联到特定已确认 Goal" — 需 tool_call_start
      携带 goal_ref 字段, AAS v0.1 schema 无此字段。宽松版只测"至少有一个
      已确认 Goal 存在", 不测"该 tool_call 属于哪个 Goal"。
    """
    goal_statement_ids: set[str] = {
        ev.event_id for ev in egress.timeline
        if ev.payload_kind == "goal_statement"
    }

    confirmed_goal_ids: set[str] = set()
    bad: list[str] = []
    for ev in egress.timeline:
        if (
            ev.payload_kind == "user_response"
            and ev.confirms_event_id is not None
            and ev.confirms_event_id in goal_statement_ids
        ):
            confirmed_goal_ids.add(ev.confirms_event_id)
        elif ev.payload_kind == "tool_call_start" and not confirmed_goal_ids:
            bad.append(ev.event_id)

    return tuple(bad)


# ──────────────────────────────────────────────────────────────────────────


def aas_core_detector() -> FalsifiableDetector:
    """规范层最小机械检测器 (AAS §E.2 反向证明化身)。

    此函数存在本身即是 §E.2 不塌的 *正向证据*: 一个能机械检测核心禁令
    的函数被写出来了, 故 §E.2 "若不可机械检测则自塌" 不触发。

    覆盖 (v0.1-draft-impl):
      §B1.2 (claim/judge 不一致)     §B3.1 (无 main J)
      §B3.2 (越级)                    §B4.1 (timeline prev_hash 形态断裂)
      §B4.3 (不可复现静默)            §B1.3 (Goal 锁定后不可变)
      §B2.1 (deny-by-default)         §B2.2 (blacklist 不可直接执行)
      §9.3 (无 Goal 不调工具)         — DESIGN.md §9.3 红蓝对抗产出
    """
    def _detect(egress: RunEgress) -> tuple[str, ...]:
        violations: list[str] = []
        real_main = _real_main_judge_termination(egress)

        if real_main is None:
            # §B3.1 未声明 main Judge → run 不合规 (mechanical detectable)
            violations.append("B3.1_no_main_judge_event_in_timeline")
        elif (
            egress.final_run_claim == "met"
            and real_main in ("undecidable", "not_met")
        ):
            # §B1.2 核心禁令: claim=met 但 main judge 给 undecidable/not_met
            violations.append(
                "B1.2_claim_met_but_judge_" + real_main
            )

        aux_violators = _aux_terminations_after_main_undecidable(egress)
        for bad_id in aux_violators:
            violations.append("B3.2_aux_escalation_after_main_undecidable:" + bad_id)

        # §B4.1 timeline 链式 prev_hash 形态断裂
        for broken_id, tl_idx in _broken_chain_event_ids(egress):
            violations.append(f"B4.1_timeline_chain_broken:{broken_id}@{tl_idx}")

        # §B4.3 不reproducible run 被静默计入reproducible分母
        if _has_chain_broken(egress) and egress.non_reproducible_cause is None:
            violations.append("B4.3_non_reproducible_silently_omitted")

        # §B1.3 Goal lock后 run 内不可变
        for bad_id, mutated_intent in _goal_mutations(egress):
            violations.append(f"B1.3_goal_mutated_after_lock:{bad_id}->{mutated_intent}")

        # §B2.1 deny-by-default (unmatched default应归 greylist 不可 allow)
        for bad_id in _deny_by_default_allows(egress):
            violations.append(f"B2.1_deny_by_default_violated:{bad_id}")

        # §B2.2 blacklist 不可直接execute
        for bad_action in _blacklist_direct_executions(egress):
            violations.append(f"B2.2_blacklist_directly_executed:{bad_action}")

        # §9.3 无 Goal 不调tool (DESIGN.md §9.3 红蓝对抗产出)
        for bad_id in _tool_calls_without_locked_goal(egress):
            violations.append(f"S9.3_tool_call_without_locked_goal:{bad_id}")

        return tuple(violations)

    return _detect


# ──────────────────────────────────────────────────────────────────────────


GENESIS = "GENESIS"
"""首条 event 的 prev_hash sentinel。规范不规定hashfunction, 但规定链式形态。"""


def _broken_chain_event_ids(egress: RunEgress) -> tuple[tuple[str, int], ...]:
    """return timeline 中链式 prev_hash *形态* 不合规的 (event_id, timeline_idx) list。

    返回 timeline_idx (而非 violations 列表中的索引) 以保证诊断价值 —
    审计者据此定位到具体哪条 event 断裂。

    机械可测的断裂 (AAS §B4.1):
      - 首条 event 的 prev_hash != GENESIS
      - 任意 event 的 prev_hash 为空字符串

    机械 *不可* 测的断裂 (本检测器故意不测, OPEN):
      - prev_hash 与前一 event 的实际哈希不一致 (需规范规定哈希函数,
        AAS v0.1 不规定, 故不可机械检测 — 见 §F).
      若假装能测, 即违反 §E.2 "不可证伪即自塌".
    """
    bad: list[tuple[str, int]] = []
    for idx, ev in enumerate(egress.timeline):
        if idx == 0:
            if ev.prev_hash != GENESIS:
                bad.append((ev.event_id, idx))
        else:
            if not ev.prev_hash:
                bad.append((ev.event_id, idx))
    return tuple(bad)


def _has_chain_broken(egress: RunEgress) -> bool:
    """判断 timeline 是否落入 non_reproducible_by_construction (§B4.3 分母)。

    AAS §2.1.4 / §B4.3: timeline 断链 = non_reproducible_by_construction 集合的
    明确一条。本函数只以此一条判, 其他 (anchor_unreachable / data_snapshot 缺)
    不在规范层机械检测能力内 — 那些属参照实现侧的运行时事实。
    """
    return len(_broken_chain_event_ids(egress)) > 0
