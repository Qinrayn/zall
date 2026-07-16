"""zall.cli.responder — 把 §4.5 confirm_gate 的 UserResponder 接进 CLI。

Corresponds to:
  §4.5   confirm_gate: greylist → 交互 accept/reject/modify/timeout
                     blacklist → 不执行原动作; user Override → 执行 + 审计
  §6.4   Override 审计 (override_text 非空, 触发 OverrideEvent)
  PR-0   blacklist 不得被 --yes 自动放行 (防线)

本模块是应用层 (非 core/), 实现 core/gate.UserResponder Protocol。
core/ 不依赖本文件; 本文件依赖 core/。

IPR constraints:
  IPR-0: invariant tests at tests/test_cli_responder.py
  IPR-1: corresponds to DESIGN.md §4.5 + §6.4 + PR-0
  IPR-3: only stdlib, no model SDK
"""

from __future__ import annotations

import sys
from typing import Any, Callable

from zall.core.action import Action
from zall.core.gate import UserResponder, UserResponse, UserResponseType
from zall.core.safety import Judgement, SafeLevel


class CliUserResponder(UserResponder):
    """CLI 交互式 user responder (§4.5 UserResponder 的 CLI 投影)。

    行为契约 (§4.5 + PR-0):
      WHITELIST  — 不会到 ask() (gate 直接 EXECUTING, 不问 user)
      GREYLIST   — 交互 [y/N/e/s]; --yes 模式自动 ACCEPT; 非 TTY 默认 REJECT
      BLACKLIST  — 默认 REJECT; 仅 user 显式 override + 非空理由才放行
                   --yes 绝不自动 override blacklist (PR-0 防线)
                   非 TTY 永远 REJECT (最安全)

    ask_fn 注入: 测试时传 fake input function; 生产用内置 input()。
    """

    __test__ = False

    def __init__(
        self,
        *,
        yes: bool = False,
        is_tty: bool | None = None,
        ask_fn: Callable[[str], str] | None = None,
        print_fn: Callable[[str], None] | None = None,
        plan_mode: bool = False,
    ) -> None:
        self._yes = yes
        # is_tty=None 时自动检测; test可显式传 False
        self._is_tty = is_tty if is_tty is not None else sys.stdin.isatty()
        self._ask = ask_fn or input
        self._print = print_fn or (lambda s: sys.stderr.write(s + "\n"))
        # v0.0.12: plan_mode (§9.2.5 只读姿态) 标注, 仅影响prompt文案
        self._plan_mode = plan_mode
        # v0.0.12: session级 "本次允许" 集合 (greylist `a` 触发) —— 不豁免 blacklist
        self._session_allow: set[str] = set()

    def clear_allow_cache(self) -> None:
        """v0.1.3: 清除session级允许cache。AgentLoop 重建时调用,
        防止上一个对话的 allow 权限泄漏到下一个对话 (B10 fix)。"""
        self._session_allow.clear()

    def ask(self, action: Action, judgement: Judgement) -> UserResponse:
        """根据 judgement.level 决定如何问 user (§4.5)。"""
        # §3.4.4 GoalDowngrade: 专gate的downgradeconfirmprompt (downgrade是 Goal 层面,
        # 不走 greylist/blacklist 通路; 之前 CliUserResponder 从不return
        # ACCEPT_DOWNGRADE, 导致downgrade特性是死代码)
        if action.tool_id == "__goal_downgrade__":
            return self._ask_downgrade(action, judgement)
        if judgement.level == SafeLevel.GREYLIST:
            # session内已被用户 "a" 允许的tool, 直接通过 (不豁免 blacklist)
            if action.tool_id in self._session_allow:
                return UserResponse(response_type=UserResponseType.ACCEPT)
            return self._ask_greylist(action, judgement)
        if judgement.level == SafeLevel.BLACKLIST:
            return self._ask_blacklist(action, judgement)
        # WHITELIST 不应到 ask() (gate 已直接 EXECUTING)。
        # 到这里说明调用方逻辑有误 —— 保守 reject (PR-0: security优先)。
        return UserResponse(response_type=UserResponseType.REJECT)

    # ── §3.4.4 GoalDowngrade: downgradeconfirm ──
    def _ask_downgrade(self, action: Action, judgement: Judgement) -> UserResponse:
        """GoalDowngrade gate: ask用户是否acceptdowngrade候选。

        与 greylist/blacklist 不同: 降级是 Goal 层面操作, 需要专门的
        ACCEPT_DOWNGRADE 响应。支持多候选选择。
        """
        # 非 TTY (pipeline/CI): default reject (与 greylist 行为一致, 最security)
        if not self._is_tty:
            self._print("  ? goal downgrade — auto-reject (non-interactive)")
            return UserResponse(response_type=UserResponseType.REJECT)

        candidates_desc = action.args.get("candidates_desc", [])
        original_type = action.args.get("original_type", "?")
        original_intent = action.args.get("original_intent", "")

        self._print("  Goal downgrade proposed:")
        self._print(f"    original [{original_type}]: {original_intent[:100]}")

        if candidates_desc and len(candidates_desc) > 0:
            self._print("    candidates:")
            for cd in candidates_desc:
                idx = cd.get("index", 0)
                gt = cd.get("goal_type", "?")
                desc = cd.get("description", "")[:80]
                self._print(f"      [{idx}] {gt} — {desc}")
            if len(candidates_desc) == 1:
                prompt = "  Accept downgrade? [y/N] "
            else:
                prompt = f"  Choose candidate [0-{len(candidates_desc)-1}] or N to reject: "
        else:
            prompt = "  Goal downgrade proposed. Accept? [y/N] "

        try:
            raw = self._ask(prompt).strip().lower()
        except (EOFError, KeyboardInterrupt):
            return UserResponse(response_type=UserResponseType.REJECT)

        if raw in ("y", "yes"):
            # 单候选或用户没指定编号 → default第一个
            return UserResponse(
                response_type=UserResponseType.ACCEPT_DOWNGRADE,
                downgrade_index=0,
            )
        if raw.isdigit() and candidates_desc:
            idx = int(raw)
            if 0 <= idx < len(candidates_desc):
                return UserResponse(
                    response_type=UserResponseType.ACCEPT_DOWNGRADE,
                    downgrade_index=idx,
                )
        return UserResponse(response_type=UserResponseType.REJECT)

    # ── greylist: 可交互, --yes 可放行 ──
    def _ask_greylist(self, action: Action, judgement: Judgement) -> UserResponse:
        # --yes pattern: 自动放行 greylist (但不放行 blacklist, 见下)
        if self._yes:
            return UserResponse(response_type=UserResponseType.ACCEPT)

        # 非 TTY (pipeline/CI): default reject (最security, 不blocking)
        if not self._is_tty:
            self._print(f"  ? {action.tool_id} greylist — auto-reject (non-interactive)")
            return UserResponse(response_type=UserResponseType.REJECT)

        # Interactive: concise confirmation (tool name/args already shown by tool_call_start)
        # 不重复打 ? greylist: bash command=... (避免两次重复)
        plan_tag = " (plan mode: read-only)" if self._plan_mode else ""
        try:
            raw = self._ask(
                f"  Allow{plan_tag}? [y/N/e/a/s] "
            ).strip().lower()
        except (EOFError, KeyboardInterrupt):
            return UserResponse(response_type=UserResponseType.REJECT)

        if raw in ("y", "yes"):
            return UserResponse(response_type=UserResponseType.ACCEPT)
        if raw in ("a", "always"):
            # 本次session允许该tool (session级; 不豁免 blacklist)
            self._session_allow.add(action.tool_id)
            self._print(f"  ✓ allowed this session: {action.tool_id}")
            return UserResponse(response_type=UserResponseType.ACCEPT)
        if raw in ("e", "edit"):
            # MODIFY: 让用户就地修改parameter, return新 Action 经 gate 重判
            modified = self._edit_action(action)
            if modified is None:
                return UserResponse(response_type=UserResponseType.REJECT)
            return UserResponse(
                response_type=UserResponseType.MODIFY,
                modified_action=modified,
            )
        if raw in ("s", "suspend"):
            return UserResponse(response_type=UserResponseType.TIMEOUT)
        # default / n / no / 空 → reject
        return UserResponse(response_type=UserResponseType.REJECT)

    def _edit_action(self, action: Action) -> Action | None:
        """greylist `e`: 就地edit action parameter, return新 Action (MODIFY 用)。

        仅支持字符串参数的工具; 非字符串参数原样保留。
        每个参数一行, 留空 = 保持原值。Ctrl-D/EOF → 取消 (返回 None)。
        """
        self._print(f"  editing {action.tool_id} (blank = keep, Ctrl-D = cancel):")
        new_args: dict[str, Any] = {}
        # 控制editsequential: command 优先 (bash 最常见)
        keys = list(action.args.keys())
        if "command" in keys:
            keys.remove("command")
            keys.insert(0, "command")
        for key in keys:
            val = action.args[key]
            if not isinstance(val, str):
                new_args[key] = val
                continue
            try:
                new_val = self._ask(f"    {key} = ").strip()
            except (EOFError, KeyboardInterrupt):
                return None
            new_args[key] = new_val if new_val else val
        return Action(tool_id=action.tool_id, args=new_args)

    # ── blacklist: default reject, override 需非空理由 ──
    def _ask_blacklist(self, action: Action, judgement: Judgement) -> UserResponse:
        # --yes pattern: blacklist 仍 REJECT, 但不交互 (PR-0 防线: --yes 不是securityswitch)
        if self._yes:
            self._print(f"  ! {action.tool_id} BLACKLIST — rejected (--yes does not override)")
            return UserResponse(response_type=UserResponseType.REJECT)

        # 非 TTY 永远 reject
        if not self._is_tty:
            self._print(f"  ! {action.tool_id} BLACKLIST — auto-reject")
            return UserResponse(response_type=UserResponseType.REJECT)

        args_preview = self._preview_args(action)
        self._print(f"\n  ! Blacklisted: {action.tool_id} {args_preview}")
        if judgement.matched_rule_ids:
            self._print(f"    rules: {', '.join(judgement.matched_rule_ids)}")
        self._print("    override with reason, or press Enter to cancel")

        try:
            reason = self._ask("  override reason (Enter=cancel): ").strip()
        except (EOFError, KeyboardInterrupt):
            reason = ""

        if not reason:
            return UserResponse(response_type=UserResponseType.REJECT)

        # 非空理由 → override (触发 §6.4 OverrideEvent audit)
        return UserResponse(
            response_type=UserResponseType.OVERRIDE,
            override_text=reason,
        )

    @staticmethod
    def _preview_args(action: Action) -> str:
        """简短预览 action.args (不泄露全部, 防 terminal 滚屏)。"""
        items = list(action.args.items())[:3]
        parts = []
        for k, v in items:
            s = str(v)
            if len(s) > 60:
                s = s[:60] + "..."
            parts.append(f"{k}={s}")
        return " ".join(parts) if parts else "(no args)"
