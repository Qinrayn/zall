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

import json
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from zall._util.string import shorten
from zall.core.action import Action
from zall.core.gate import UserResponder, UserResponse, UserResponseType
from zall.core.safety import Judgement, SafeLevel


def _always_allow_path() -> Path:
    """Return the path to the persistent always-allow permissions file."""
    from zall._util.win32 import resolve_home_dir
    return resolve_home_dir() / ".zall" / "always_allow.json"


def flush_stdin_typeahead() -> None:
    """冲刷 OS stdin 输入缓冲里滞留的按键 (type-ahead 防护, REPL 路径)。

    风险与 TUI 选择菜单同源: 模型运行时用户提前敲的 "y↵" 滞留在行缓冲,
    确认提示一出现就被立即消费 — 误批写盘操作。提问前丢弃滞留输入。

    平台实现: Windows 用 msvcrt.kbhit/getwch 排空; POSIX 用 termios.tcflush。
    非 TTY / 不支持环境静默跳过 (测试管道不受影响)。
    """
    try:
        if not sys.stdin.isatty():
            return
        if os.name == "nt":
            import msvcrt
            while msvcrt.kbhit():  # type: ignore[attr-defined]
                msvcrt.getwch()  # type: ignore[attr-defined]
        else:
            import termios
            termios.tcflush(sys.stdin.fileno(), termios.TCIFLUSH)
    except Exception:
        pass  # 防护失败不得阻断确认流程 (降级为无防护, 不崩)


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
        choose_fn: Callable[[list[tuple[str, str, str]]], str] | None = None,
    ) -> None:
        self._yes = yes
        # is_tty=None 时自动检测; test可显式传 False
        self._is_tty = is_tty if is_tty is not None else sys.stdin.isatty()
        self._ask = ask_fn or input
        self._print = print_fn or (lambda s: sys.stderr.write(s + "\n"))
        # 可选择菜单收集器 (方向/数字键); None → 文本回退 [y/n/a/e]。
        # 只影响"怎么收集主选择", 不改 _ask_greylist 的 raw->response 映射 (决策唯一真源)。
        self._choose_fn = choose_fn
        # v0.0.12: plan_mode (§9.2.5 只读姿态) 标注, 仅影响prompt文案
        self._plan_mode = plan_mode
        # v0.0.12: session级 "本次允许" 集合 (greylist `a` 触发) —— 不豁免 blacklist
        self._session_allow: set[str] = set()
        # E4: 跨会话持久化 always_allow 集合
        self._persistent_allow: set[str] = set()
        self._load_always_allow()

    def clear_allow_cache(self) -> None:
        """v0.1.3: 清除session级允许cache。AgentLoop 重建时调用,
        防止上一个对话的 allow 权限泄漏到下一个对话 (B10 fix)。"""
        self._session_allow.clear()
        # E4: 不清除 _persistent_allow (跨会话持久化, 需要显式 /forget-permissions)

    # ── E4: 跨会话权限持久化 ──

    def clear_always_allow(self) -> None:
        """E4: 清除所有持久化权限 (对应 /forget-permissions 命令)。"""
        self._persistent_allow.clear()
        self._session_allow.clear()
        self._save_always_allow()

    def _load_always_allow(self) -> None:
        """E4: 从磁盘加载 always_allow 权限集。"""
        try:
            path = _always_allow_path()
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
                raw = data.get("tool_ids", [])
                if isinstance(raw, list):
                    self._persistent_allow = {str(t) for t in raw if t}
        except Exception:
            self._persistent_allow = set()

    def _save_always_allow(self) -> None:
        """E4: 持久化 always_allow 权限集到磁盘 (原子写入)."""
        try:
            path = _always_allow_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            data = {"tool_ids": sorted(self._persistent_allow)}
            # E4: 原子写入 (tmp + os.replace), 防止中断损坏文件
            tmp_path = path.with_suffix(".json.tmp")
            tmp_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(str(tmp_path), str(path))
        except Exception:
            pass

    def ask(self, action: Action, judgement: Judgement) -> UserResponse:
        """根据 judgement.level 决定如何问 user (§4.5)。"""
        # §3.4.4 GoalDowngrade: 专gate的downgradeconfirmprompt (downgrade是 Goal 层面,
        # 不走 greylist/blacklist 通路; 之前 CliUserResponder 从不return
        # ACCEPT_DOWNGRADE, 导致downgrade特性是死代码)
        if action.tool_id == "__goal_downgrade__":
            return self._ask_downgrade(action, judgement)
        if judgement.level == SafeLevel.GREYLIST:
            # E4: 检查持久化 + session级 allow (不豁免 blacklist)
            if action.tool_id in self._persistent_allow or action.tool_id in self._session_allow:
                return UserResponse(response_type=UserResponseType.ACCEPT)
            return self._ask_greylist(action, judgement)
        if judgement.level == SafeLevel.BLACKLIST:
            return self._ask_blacklist(action, judgement)
        # WHITELIST 不应到 ask() (gate 已直接 EXECUTING)。
        # 到这里说明调用方逻辑有误 -- 保守 reject (PR-0: security优先)。
        return UserResponse(response_type=UserResponseType.REJECT)

    def _render_permission_panel(self, action: Action, level: str) -> None:
        """v1.2 交互优化 (借鉴 Claude Code): 用 Panel 显示权限请求详情。

        每工具专属渲染:
          - bash: 显示完整命令 (截断至 5 行), 语法高亮
          - edit_file/write_file: 显示 diff 预览或内容前 10 行
          - read_file/grep/glob: 显示 path + pattern
          - 其他: 关键参数摘要
        非 TTY 不渲染 (避免污染管道输出)。
        """
        if not self._is_tty:
            return
        try:
            from rich.text import Text
            args = action.args or {}
            tool_id = action.tool_id
            color = "dark_orange" if level == "greylist" else "red3 bold"
            icon = "\u26a0" if level == "greylist" else "\u2717"

            text = Text()
            text.append(f"{icon} ", style=color)
            text.append(f"{tool_id} ", style="gold1 bold")
            text.append(f"[{level}]", style=color)

            # v1.2: 每工具专属渲染 (借鉴 Claude Code per-tool permission dialogs)
            if tool_id == "bash" and "command" in args:
                cmd = str(args["command"])
                cmd_lines = cmd.split("\n")[:5]
                cmd_display = "\n".join(cmd_lines)
                if len(cmd_lines) < len(cmd.split("\n")):
                    cmd_display += f"\n  ... ({len(cmd.split(chr(10)))} lines total)"
                text.append(f"\n  $ {cmd_display}", style="grey82")
            elif tool_id in ("edit_file", "write_file", "batch_edit"):
                path = args.get("path") or args.get("file_path", "")
                if path:
                    text.append(f"\n  path: {path}", style="steel_blue1")
                # G1 (kimi 对标): 审批时从 args 现算 diff 预览 — 此前 args 无 "diff"
                # 键, 用户审批 edit_file 时看不到会改什么 (审批盲区)。
                extra = self._build_edit_preview(tool_id, args)
                if extra is not None:
                    self._print_panel_group(text, extra, border_color=color)
                    return
                content = args.get("content", "")
                diff = args.get("diff", "")
                if diff:
                    diff_lines = str(diff).split("\n")[:15]
                    text.append("\n", style="")
                    for dl in diff_lines:
                        if dl.startswith("+") and not dl.startswith("+++"):
                            text.append(f"  {dl}\n", style="green")
                        elif dl.startswith("-") and not dl.startswith("---"):
                            text.append(f"  {dl}\n", style="red")
                        elif dl.startswith("@@"):
                            text.append(f"  {dl}\n", style="cyan")
                        else:
                            text.append(f"  {dl}\n", style="grey50")
                    if len(str(diff).split("\n")) > 15:
                        text.append("  ...", style="grey50")
                elif content:
                    content_lines = str(content).split("\n")[:10]
                    text.append("\n", style="")
                    for cl in content_lines:
                        text.append(f"  {cl[:80]}\n", style="grey70")
                    if len(str(content).split("\n")) > 10:
                        text.append("  ...", style="grey50")
            elif tool_id in ("read_file", "grep", "glob", "list_dir", "search"):
                path = args.get("path") or args.get("file_path", "")
                pattern = args.get("pattern") or args.get("query", "")
                if path:
                    text.append(f"\n  path: {path}", style="steel_blue1")
                if pattern:
                    text.append(f"\n  pattern: {pattern}", style="grey70")
            else:
                # 通用: 关键参数摘要
                key_arg = ", ".join(f"{k}={str(v)[:60]}" for k, v in list(args.items())[:3])
                text.append(f"\n  {key_arg}", style="grey70")

            self._print_panel(text, border_color=color)
        except Exception:
            # 渲染失败不阻塞权限流程, 降级为纯文本
            self._print(f"  ? {action.tool_id} [{level}]")

    def _build_edit_preview(self, tool_id: str, args: dict) -> list | None:
        """G1: 从工具入参现算审批 diff 预览 (紧凑形态, 只显改动行)。

        edit_file: old/new_string 全量在 args; 读文件定位真实行号 (失败从 1 计)。
        batch_edit: 每 edit 一段 mini 预览, 最多 3 段。
        返回 None = 不适用 (回退旧渲染)。
        """
        try:
            from zall.cli import diff_render as _dr
            if tool_id == "edit_file" and args.get("old_string"):
                old = str(args.get("old_string", ""))
                new = str(args.get("new_string", ""))
                start = self._locate_start_line(str(args.get("path", "")), old)
                return _dr.preview_from_texts(
                    str(args.get("path", "")), old, new,
                    old_start=start, new_start=start,
                )
            if tool_id == "batch_edit" and isinstance(args.get("edits"), list):
                out: list = []
                edits = args["edits"]
                for edit in edits[:3]:
                    if not isinstance(edit, dict):
                        continue
                    old = str(edit.get("old_string", ""))
                    new = str(edit.get("new_string", ""))
                    p = str(edit.get("path", ""))
                    start = self._locate_start_line(p, old)
                    out.extend(_dr.preview_from_texts(
                        p, old, new, old_start=start, new_start=start, max_lines=3,
                    ))
                if len(edits) > 3:
                    from rich.text import Text as _T
                    out.append(_T(f"... {len(edits) - 3} more edits", style="dim italic"))
                return out or None
        except Exception:
            return None
        return None

    @staticmethod
    def _locate_start_line(path: str, old_string: str) -> int:
        """读文件定位 old_string 的真实起始行号; 失败返 1 (预览仍可用)。"""
        try:
            from zall._util import read_text_file
            content = read_text_file(Path(path))
            idx = content.find(old_string)
            if idx >= 0:
                return content[:idx].count("\n") + 1
        except Exception:
            pass
        return 1

    def _print_panel_group(self, header, renderables: list, border_color: str) -> None:
        """渲染 header + diff 预览组合 Panel (降级安全)。"""
        try:
            from rich.console import Console, Group
            from rich.panel import Panel
            console = Console(stderr=True)
            console.print(Panel(
                Group(header, *renderables),
                border_style=border_color, padding=(0, 1),
            ))
        except Exception:
            self._print(str(header))

    def _print_panel(self, text, border_color: str) -> None:
        """渲染 rich Panel (降级安全)。"""
        try:
            from rich.console import Console
            from rich.panel import Panel
            console = Console(stderr=True)
            console.print(Panel(text, border_style=border_color, padding=(0, 1)))
        except Exception:
            self._print(str(text))

    # ── §3.4.4 GoalDowngrade: downgradeconfirm ──
    def _ask_downgrade(self, action: Action, judgement: Judgement) -> UserResponse:
        """GoalDowngrade gate: ask用户是否acceptdowngrade候选。

        与 greylist/blacklist 不同: 降级是 Goal 层面操作, 需要专门的
        ACCEPT_DOWNGRADE 响应。支持多候选选择。

        --yes 模式: auto-reject (保持 Goal 不降级, 与 greylist 行为一致)。
        """
        # --yes pattern: auto-reject (保持原 Goal, 不阻塞 workflow)
        if self._yes:
            self._print("  ? goal downgrade — auto-reject (--yes)")
            return UserResponse(response_type=UserResponseType.REJECT)

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
            self._print(f"  ? {action.tool_id} greylist - auto-reject (non-interactive)")
            return UserResponse(response_type=UserResponseType.REJECT)

        # v1.2 交互优化 (借鉴 Claude Code): 用 Panel 显示权限请求详情
        # 让用户看清要允许什么工具 + 关键参数, 而非只看 "Allow? [y/N]"
        self._render_permission_panel(action, "greylist")

        # type-ahead 防护 (与 TUI 选择菜单同源): 冲刷模型运行时用户提前敲的
        # 滞留按键, 避免 "y↵" 误批写盘。仅交互 TTY 生效, 注入了 ask_fn 时跳过
        # (测试/prompt_toolkit 栈自管输入, 不碰 OS 缓冲)。
        if self._ask is input:
            flush_stdin_typeahead()

        raw = self._collect_greylist_choice()

        if raw in ("y", "yes"):
            return UserResponse(response_type=UserResponseType.ACCEPT)
        if raw in ("a", "always"):
            # E4: 跨会话持久化允许该tool
            self._session_allow.add(action.tool_id)
            self._persistent_allow.add(action.tool_id)
            self._save_always_allow()
            self._print(f"  \u2713 always allowed: {action.tool_id}")
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
        if raw in ("f", "feedback", "why"):
            # G2 (kimi 对标): 拒绝 + 自由文本理由 — 模型知道该怎么改。
            # 空理由退化为纯拒绝 (反例路径)。
            try:
                fb = self._ask("  why? (tell the model what to do instead): ").strip()
            except (EOFError, KeyboardInterrupt):
                fb = ""
            return UserResponse(
                response_type=UserResponseType.REJECT,
                feedback=fb or None,
            )
        # default / n / no / 空 → reject
        return UserResponse(response_type=UserResponseType.REJECT)

    # greylist 主选择项 (value 必须匹配下方 _ask_greylist 的 raw 解析)
    _GREYLIST_CHOICES: list[tuple[str, str, str]] = [
        ("y", "allow once", "run this tool call"),
        ("n", "reject", "skip this tool call"),
        ("f", "reject + why", "reject and tell the model what to do instead"),
        ("a", "always allow", "auto-allow this tool this session"),
        ("e", "edit params", "modify parameters then decide"),
    ]

    def _collect_greylist_choice(self) -> str:
        """收集 greylist 主选择, 返回 y/n/a/e/s 之一 (小写)。

        choose_fn (方向/数字键选择器) 优先; 否则文本回退 (原 [y]es[n]o... + Allow?)。
        空/异常/EOF → 'n' (reject, 安全默认)。决策映射仍在 _ask_greylist (唯一真源)。
        """
        if self._choose_fn is not None:
            try:
                return (self._choose_fn(self._GREYLIST_CHOICES) or "n").strip().lower()
            except Exception:
                return "n"
        if self._is_tty:
            self._print("  [y]es  [n]o  [f]eedback-reject  [a]lways allow  [e]dit params  [s]kip")
        plan_tag = " (plan mode: read-only)" if self._plan_mode else ""
        try:
            return self._ask(f"  Allow{plan_tag}? ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return "n"

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
            s = shorten(str(v), width=60, placeholder="...")
            parts.append(f"{k}={s}")
        return " ".join(parts) if parts else "(no args)"
