"""zall.core.safety — context_judge rule engine (DESIGN.md §4.2.1).

Corresponds to:
  §4.2.1  context_judge(action, context) -> SafeLevel
          声明式规则引擎 + deny 优先 + 无匹配默认 greylist
  §4.2.2  context_judge 不是 agent (不引模型, 守 PR-3)
  §4.2.3  与 §4.5 confirm_gate 复用 (不引层级)
  §4.2.4  残余 OPEN: match_all grammar deferred / 优先级链 polish deferred

IPR constraints:
  IPR-0: invariant tests at tests/test_context_judge_invariants.py, includesCounterexample
  IPR-1: this file corresponds to DESIGN.md §4.2.1-4.2.4
  IPR-3: pydantic / stdlib only, no model SDK
  IPR-4: this file is a primitive, no main Loop
"""

from __future__ import annotations

from enum import Enum
from fnmatch import translate
from functools import lru_cache
import re

from pydantic import BaseModel, ConfigDict, model_validator

from zall.core.action import Action
from zall.core.context import Context


# ──────────────────────────────────────────────────────────────────────────
# §4.2.1 SafeLevel (three-state, 不开 4 态 over-engineering)
# ──────────────────────────────────────────────────────────────────────────


class SafeLevel(str, Enum):
    """context_judge 的three-statereturn (DESIGN.md §4.2.1)。

    v0.0.7 红蓝对抗已驳掉 4 态 over-engineering:
    unresolvable 不作独立返回值, 只作 greylist 的子状态。
    """

    WHITELIST = "whitelist"
    GREYLIST = "greylist"
    BLACKLIST = "blacklist"


# greylist 子state (only audit 用, 不参与逻辑分stream)
GREYLIST_SUB_UNRESOLVABLE = "greylist_unresolvable_no_rule_matched"


# ──────────────────────────────────────────────────────────────────────────
# §4.2.1 Rule (声明式, 数据不是代码)
# ──────────────────────────────────────────────────────────────────────────


class Rule(BaseModel):
    """一条声明式rule (DESIGN.md §4.2.1)。

    规则是**数据**, 不是代码:
      - tool_id_pattern: fnmatch glob 匹配 tool_id (eg. "bash", "*")
      - args_matcher:    dict[str, str] —— key 是 args 的键,
                         value 是 substring 匹配 (eg. {"command": "push"})
      - context_matcher: dict[str, str] —— key 是 Context 的属性路径
                         (eg. {"cwd_meta.git_branch": "main"})
      - level:           命中时的 SafeLevel

    任何加复杂逻辑的企图 (eg. 调用模型 / 执行任意代码) 都是偷渡,
    违反 §4.2.2 "context_judge 不是 agent"。

    match_all grammar constraints (§4.2.4 deferred 的部分收窄):
      - only fnmatch + substring, 不开 dynamic eval
      - 复杂判定留给 user 的 rules.toml 扩展 (deferred)
    """

    model_config = ConfigDict(frozen=True)

    rule_id: str
    tool_id_pattern: str
    args_matcher: dict[str, str] = {}
    context_matcher: dict[str, str] = {}
    level: SafeLevel
    description: str = ""

    def matches(self, action: Action, context: Context) -> bool:
        """判断此rule是否匹配 (action, context)。

        纯函数: 不修改 action / context, 不调外部服务, 不引模型。

        v0.0.26: args_matcher 使用单词边界匹配 (\b), 避免 "kill" 误匹配 "skill"。
        context_matcher 保持简单子串匹配 (路径值无此歧义)。
        """
        # 1. tool_id glob 匹配 (O2: 使用cache的预编译 regex 替代 fnmatch)
        if not _fnmatch_to_re(self.tool_id_pattern).match(action.tool_id):
            return False

        # 2. args 单词边界匹配 (v0.0.26: 用 regex 避免子串歧义)
        for key, pattern in self.args_matcher.items():
            val = action.args.get(key)
            if val is None:
                return False
            if not _word_boundary_match(pattern, str(val)):
                return False

        # 3. context propertypath匹配 (v0.0.27: 改用单词边界匹配避免子串歧义)
        for path, pattern in self.context_matcher.items():
            val = _resolve_context_path(context, path)
            if val is None:
                return False
            if not _word_boundary_match(pattern, str(val)):
                return False

        return True


# v0.0.26: 预编译的patterncache (performance: 避免每次匹配都 re.compile)
# v0.1.1: 使用 lru_cache 替代手动 dict, 防止无限增长
@lru_cache(maxsize=128)
def _compile_word_boundary_pattern(pattern: str) -> re.Pattern[str]:
    """编译单词边界匹配的正则 (带 LRU cache, O1 optimize)。"""
    escaped = re.escape(pattern)
    prefix = r"\b" if escaped[:1].isalnum() else ""
    suffix = r"\b" if escaped[-1:].isalnum() else ""
    return re.compile(prefix + escaped + suffix, re.IGNORECASE)


@lru_cache(maxsize=256)
def _fnmatch_to_re(pattern: str) -> re.Pattern[str]:
    """将 fnmatch glob pattern 转为编译好的 regex, 带 LRU cache (O2 optimize)。

    避免每次 Rule.matches() 调用都 fnmatch → translate → re.compile 的三步开销。
    fnmatch 内部用 translate() 转 regex 再 compile, 缓存此结果可复用相同 pattern。

    S1 fix: 加 ^(?:...) 和 $ 锚定, 防止 tool_id_pattern="bash" 误配 "bash_tool"。
    """
    return re.compile("^(?:" + translate(pattern) + ")$")


def _word_boundary_match(pattern: str, value: str) -> bool:
    """单词边界匹配: check pattern 是否出现在 value 中的单词边界上。

    使用 \b 确保 pattern 不是较长单词的子串:
      - "kill -" 匹配 "kill -9" ✓
      - "kill -" 不匹配 "skill --version" ✓ (因为 's' 在 'k' 前)
      - "format " 匹配 "format C:" ✓
      - "format " 不匹配 "deformat " ✓
    """
    # v0.1.1: 使用 lru_cache 替代手动 dict, 防止无限增长
    return _compile_word_boundary_pattern(pattern).search(value) is not None


def _resolve_context_path(context: Context, path: str) -> object:
    """按点分path取 Context property (eg. "cwd_meta.git_branch")。

    纯查找, 不执行任何代码。
    """
    obj: object = context
    for part in path.split("."):
        if obj is None:
            return None
        obj = getattr(obj, part, None)
    return obj


# ──────────────────────────────────────────────────────────────────────────
# §4.2.1 Judgement (return值: SafeLevel + 子state + 命中rule)
# ──────────────────────────────────────────────────────────────────────────


class Judgement(BaseModel):
    """context_judge 的return值 (DESIGN.md §4.2.1)。

    不只是 SafeLevel three-state, 还includes:
      - matched_rule_ids: 哪些规则命中了 (audit 用)
      - sub_status:       greylist 子状态 (eg. greylist_unresolvable_no_rule_matched)
                          only audit 用, 不参与逻辑分流

    IPR-0 不变量:
        - frozen
    """

    model_config = ConfigDict(frozen=True)

    level: SafeLevel
    matched_rule_ids: tuple[str, ...] = ()
    sub_status: str | None = None


# ──────────────────────────────────────────────────────────────────────────
# §4.2.1 rule集 (三层来源 + 优先级链)
# ──────────────────────────────────────────────────────────────────────────


class RuleSet(BaseModel):
    """Rule set (DESIGN.md §4.2.1 declared_rules).

    Three rule tiers (§4.2.1):
        core_deny_rules:     Immutable deny-rules (hardcoded + published)
        user_local_rules:    Project/.zall/rules.toml (user overridable)
        domain_rules:        AgentType domain knowledge constants
        greylist_deny_rules: Item F: 中等危险规则, 命中时返回 GREYLIST 而非 BLACKLIST
                             (用户看到 Allow? 提示, 而非需要 override)

    Priority chain (deny-first):
        immutable core_deny > user_local.deny > user_local.allow > domain.allow
        greylist_deny: 在无匹配时作为最后防线, 返回 GREYLIST

    Two-pass scan + greylist_deny fallback (in context_judge function):
        Pass 1: core_deny_rules — any hit → BLACKLIST, ignoring other tiers
        Pass 2: user_local_rules + domain_rules — merged deny-first
        Pass 3 (Item F): greylist_deny_rules — any hit → GREYLIST (prompt user)

    IPR-0 invariant:
        - core_deny_rules must only contain level=BLACKLIST rules
          (core denies are immutable; whitelist/greylist in core_deny would break
           the priority chain)

    Counterexample: adding a whitelist rule to core_deny_rules must raise.
    """

    model_config = ConfigDict(frozen=True)

    core_deny_rules: tuple[Rule, ...] = ()
    user_local_rules: tuple[Rule, ...] = ()
    domain_rules: tuple[Rule, ...] = ()
    greylist_deny_rules: tuple[Rule, ...] = ()  # Item F: 中等危险规则

    @model_validator(mode="after")
    def _core_deny_must_be_blacklist(self) -> "RuleSet":
        """Core deny-rules can only be BLACKLIST (§4.2.1 priority chain)."""
        for rule in self.core_deny_rules:
            if rule.level != SafeLevel.BLACKLIST:
                raise ValueError(
                    f"core_deny_rules can only contain BLACKLIST rules, "
                    f"but rule_id={rule.rule_id} has level={rule.level.value}"
                )
        return self

    @model_validator(mode="after")
    def _greylist_deny_ids_must_be_unique(self) -> "RuleSet":
        """Item F: greylist_deny_rules 的 rule_id 不能与 core_deny 重复。"""
        core_ids = {r.rule_id for r in self.core_deny_rules}
        for rule in self.greylist_deny_rules:
            if rule.rule_id in core_ids:
                raise ValueError(
                    f"greylist_deny_rules rule_id={rule.rule_id} "
                    f"already exists in core_deny_rules"
                )
        return self


# ──────────────────────────────────────────────────────────────────────────
# §4.2.1 context_judge 主function
# ──────────────────────────────────────────────────────────────────────────


def context_judge(action: Action, context: Context, rules: RuleSet) -> Judgement:
    """声明式rule引擎 (DESIGN.md §4.2.1)。

    不解语义, 不引模型, 不调外部服务 (§4.2.2)。
    两趟扫描: core_deny 先扫, user/domain 后扫。
    无匹配 → greylist + sub_status=greylist_unresolvable_no_rule_matched。

    返回 Judgement (不是裸 SafeLevel)。
    """
    # ── 第一趟: core_deny_rules (任何命中 → BLACKLIST, 不看其他层)
    core_hits = [r for r in rules.core_deny_rules if r.matches(action, context)]
    if core_hits:
        return Judgement(
            level=SafeLevel.BLACKLIST,
            matched_rule_ids=tuple(r.rule_id for r in core_hits),
        )

    # ── 第二趟: user_local_rules + domain_rules, deny 优先
    # deny 优先: 命中 BLACKLIST 立即返回, 不继续匹配
    user_domain: tuple[Rule, ...] = rules.user_local_rules + rules.domain_rules
    grey_hits: list[Rule] = []
    white_hits: list[Rule] = []
    for r in user_domain:
        if r.matches(action, context):
            if r.level == SafeLevel.BLACKLIST:
                return Judgement(
                    level=SafeLevel.BLACKLIST,
                    matched_rule_ids=(r.rule_id,),
                )
            elif r.level == SafeLevel.GREYLIST:
                grey_hits.append(r)
            elif r.level == SafeLevel.WHITELIST:
                white_hits.append(r)

    if grey_hits:
        return Judgement(
            level=SafeLevel.GREYLIST,
            matched_rule_ids=tuple(r.rule_id for r in grey_hits),
        )

    if white_hits:
        return Judgement(
            level=SafeLevel.WHITELIST,
            matched_rule_ids=tuple(r.rule_id for r in white_hits),
        )

    # ── Item F: 第三趟 — greylist_deny_rules (中等危险, promptconfirm)
    grey_deny_hits = [r for r in rules.greylist_deny_rules if r.matches(action, context)]
    if grey_deny_hits:
        return Judgement(
            level=SafeLevel.GREYLIST,
            matched_rule_ids=tuple(r.rule_id for r in grey_deny_hits),
            sub_status="greylist_deny_hit",
        )

    # ── 无匹配 → default greylist (不default whitelist)
    return Judgement(
        level=SafeLevel.GREYLIST,
        matched_rule_ids=(),
        sub_status=GREYLIST_SUB_UNRESOLVABLE,
    )
