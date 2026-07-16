"""zall.safety.rules_file — context_judge rule file loader.

对应设计: §4.2.1 declared_rules = 核心immutable deny + user_local + domain
         §4.6 "context_judge 规则可外化配置"

Loads .zall/rules.toml (project-level) or ~/.zall/rules.toml (user-level).
Rule format:
    [[rules]]
    id = "deny_push_to_main"
    tool_id = "bash"
    args = { command = "push" }
    context = { "cwd_meta.git_branch" = "main" }
    level = "blacklist"

    [[rules]]
    id = "allow_read"
    tool_id = "read_file"
    level = "whitelist"

IPR constraints:
    IPR-0: invariant test在 tests/test_rules_file_invariants.py
    IPR-1: corresponds to DESIGN.md §4.2.1
    IPR-3: only stdlib, no model SDK
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from zall.core.safety import Rule, RuleSet, SafeLevel
from zall._util.toml import unquote_value


# P8: 模块级常量, 避免每次 _parse_level 调用创建 dict
_LEVEL_MAP: dict[str, SafeLevel] = {
    "whitelist": SafeLevel.WHITELIST,
    "greylist": SafeLevel.GREYLIST,
    "blacklist": SafeLevel.BLACKLIST,
}


def _parse_level(raw: str) -> SafeLevel:
    return _LEVEL_MAP.get(raw.lower(), SafeLevel.GREYLIST)


def _load_toml_simple(path: Path) -> dict[str, Any]:
    """简易 TOML parse (only支持 [[rules]] 数组)。

    不依赖第三方 TOML 库 (守 IPR-3: 轻依赖)。
    """
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    return _parse_toml_like(text)


def _parse_toml_like(text: str) -> dict[str, Any]:
    """parse简易 TOML 格式的 rules 数组。

    支持格式:
        [[rules]]
        id = "..."
        tool_id = "..."
        args = { key = "value" }
        context = { key = "value" }
        level = "blacklist"
    """
    # P7: 提取共用parsefunction, 消除 args/context 双份代码
    def _parse_single_line_dict(line: str, prefix: str) -> dict[str, Any]:
        """parse单行 key = { k1 = "v1", k2 = "v2" } 格式。支持带引号/点号的键。"""
        inner = line[len(prefix):-1].strip().strip("}")
        result: dict[str, Any] = {}
        if inner:
            import re as _re
            # Match both quoted keys ("cwd_meta.git_branch") and bare keys (k1, k2)
            for kv_match in _re.finditer(r'(?:"([^"]+)"|\'([^\']+)\'|(\w+))\s*=\s*("[^"]*"|\'[^\']*\'|\S+)', inner):
                k = kv_match.group(1) or kv_match.group(2) or kv_match.group(3)
                v = unquote_value(kv_match.group(4))
                result[k] = v
        return result
    rules: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    in_args = False
    in_ctx = False

    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        if stripped == "[[rules]]":
            if current:
                rules.append(current)
            current = {}
            in_args = False
            in_ctx = False
            continue

        if current is None:
            continue

        if stripped == "args = {":
            current["args"] = {}
            in_args = True
            continue
        if stripped == "context = {":
            current["context"] = {}
            in_ctx = True
            continue
        # P7: 用共用functionhandle单行 args/context
        if stripped.startswith("args = {") and stripped.endswith("}"):
            current["args"] = _parse_single_line_dict(stripped, "args = {")
            continue
        if stripped.startswith("context = {") and stripped.endswith("}"):
            current["context"] = _parse_single_line_dict(stripped, "context = {")
            continue
        if stripped == "}" and (in_args or in_ctx):
            in_args = False
            in_ctx = False
            continue

        if "=" in stripped:
            key, _, val = stripped.partition("=")
            key = key.strip()
            val = unquote_value(val.strip())

            if in_args and "args" in current:
                current["args"][key] = val
            elif in_ctx and "context" in current:
                current["context"][key] = val
            else:
                current[key] = val

    if current:
        rules.append(current)

    return {"rules": rules}


def _default_safe_rules() -> list[Rule]:
    """开箱即用的securitydefaultrule (当无 user/project rules.toml 时inject)。

    Design philosophy:
      - 只读工具 (read/grep/glob/list_dir/bash) → whitelist (免确认)
      - 写操作 (write_file/edit_file) → greylist (确认)
      - 危险 bash (rm -rf / push to main) → core_deny 黑名单兜底 (已在 load_rules)

    bash 全白名单的风险: 危险命令 (del/format/shutdown) 无规则兜底。
    折中: core_deny 已拦 rm -rf + push; 其余信任用户 (用户可在 rules.toml 加 deny)。
    这比"每次都问"实用得多 (用户实测: 20 步按 20 次 y 不可用)。
    """
    return [
        Rule(rule_id="default_allow_read", tool_id_pattern="read_file",
             args_matcher={}, context_matcher={}, level=SafeLevel.WHITELIST),
        Rule(rule_id="default_allow_grep", tool_id_pattern="grep",
             args_matcher={}, context_matcher={}, level=SafeLevel.WHITELIST),
        Rule(rule_id="default_allow_glob", tool_id_pattern="glob",
             args_matcher={}, context_matcher={}, level=SafeLevel.WHITELIST),
        Rule(rule_id="default_allow_list", tool_id_pattern="list_dir",
             args_matcher={}, context_matcher={}, level=SafeLevel.WHITELIST),
        Rule(rule_id="default_allow_bash", tool_id_pattern="bash",
             args_matcher={}, context_matcher={}, level=SafeLevel.WHITELIST),
        Rule(rule_id="default_confirm_write", tool_id_pattern="write_file",
             args_matcher={}, context_matcher={}, level=SafeLevel.GREYLIST),
        Rule(rule_id="default_confirm_edit", tool_id_pattern="edit_file",
             args_matcher={}, context_matcher={}, level=SafeLevel.GREYLIST),
        Rule(rule_id="default_confirm_spawn", tool_id_pattern="spawn_subagent",
             args_matcher={}, context_matcher={}, level=SafeLevel.GREYLIST,
             description="生成子 agent (GREYLIST: 需确认, 涉及权限代理)"),
    ]


def load_rules(
    project_path: str | None = None,
    user_path: str | None = None,
) -> RuleSet:
    """loadrulefile。

    优先级: 项目级 > 用户级 > sensible defaults > 核心 deny (核心 deny 在 RuleSet 中硬编码)

    核心 deny-rules (不可被 user 覆盖):
        - 禁止跨工作目录删除
        - 禁止 git push --force 到 main/master

    无 user/project rules.toml 时, 注入 _default_safe_rules (开箱即用安全默认)。
    """
    core_deny: list[Rule] = [
        Rule(
            rule_id="core_deny_rm_rf_root",
            tool_id_pattern="bash",
            args_matcher={"command": "rm -rf /"},
            context_matcher={},
            level=SafeLevel.BLACKLIST,
        ),
        Rule(
            rule_id="core_deny_rm_rf_variant",
            tool_id_pattern="bash",
            args_matcher={"command": "rm -rfv"},
            context_matcher={},
            level=SafeLevel.BLACKLIST,
        ),
        Rule(
            rule_id="core_deny_rm_rf_no_preserve_root",
            tool_id_pattern="bash",
            args_matcher={"command": "rm -rf --no-preserve-root"},
            context_matcher={},
            level=SafeLevel.BLACKLIST,
        ),
        Rule(
            rule_id="core_deny_rm_rf_dir",
            tool_id_pattern="bash",
            args_matcher={"command": "rm -rf "},
            context_matcher={},
            level=SafeLevel.BLACKLIST,
        ),
        Rule(
            rule_id="core_deny_push_force_main",
            tool_id_pattern="bash",
            args_matcher={"command": "push"},
            context_matcher={"cwd_meta.git_branch": "main"},
            level=SafeLevel.BLACKLIST,
        ),
        Rule(
            rule_id="core_deny_push_force_master",
            tool_id_pattern="bash",
            args_matcher={"command": "push"},
            context_matcher={"cwd_meta.git_branch": "master"},
            level=SafeLevel.BLACKLIST,
        ),
        # v0.0.10/v0.0.25: Windows 危险command补充
        # v0.0.25 fix: command前缀匹配 (带空格/斜杠), 避免 where/format, where/shutdown,
        # where/kill 等探测command被子字符串误判。
        Rule(
            rule_id="core_deny_del_force_recursive",
            tool_id_pattern="bash",
            args_matcher={"command": "del /f /s"},
            context_matcher={},
            level=SafeLevel.BLACKLIST,
        ),
        Rule(
            rule_id="core_deny_format",
            tool_id_pattern="bash",
            args_matcher={"command": "format "},  # 带空格: 匹配 "format C:" 不匹配 "where format"
            context_matcher={},
            level=SafeLevel.BLACKLIST,
        ),
        Rule(
            rule_id="core_deny_shutdown",
            tool_id_pattern="bash",
            args_matcher={"command": "shutdown "},  # 带空格: 匹配 "shutdown /s" 不匹配 "where shutdown"
            context_matcher={},
            level=SafeLevel.BLACKLIST,
        ),
        # Unix 危险command补充
        Rule(
            rule_id="core_deny_dd_disk",
            tool_id_pattern="bash",
            args_matcher={"command": "dd if="},
            context_matcher={},
            level=SafeLevel.BLACKLIST,
        ),
        Rule(
            rule_id="core_deny_mkfs",
            tool_id_pattern="bash",
            args_matcher={"command": "mkfs"},  # mkfs 本身极少出现在其它命令里, 安全
            context_matcher={},
            level=SafeLevel.BLACKLIST,
        ),
        Rule(
            rule_id="core_deny_chmod_777_recursive",
            tool_id_pattern="bash",
            args_matcher={"command": "chmod -R 777"},
            context_matcher={},
            level=SafeLevel.BLACKLIST,
        ),
        # v0.0.23/v0.0.25: processterminatecommand (agent 不得terminate自身或其它process)
        # v0.0.25 fix: 用 "taskkill " (带空格) 避免 "where taskkill" 误判;
        # "kill" 改为 "kill -" / "kill " (带空格) 避免 "skill" 误判;
        # "pkill"/"killall" 本身无歧义, preserve。
        Rule(
            rule_id="core_deny_taskkill",
            tool_id_pattern="bash",
            args_matcher={"command": "taskkill "},  # 带空格
            context_matcher={},
            level=SafeLevel.BLACKLIST,
        ),
        Rule(
            rule_id="core_deny_tskill",
            tool_id_pattern="bash",
            args_matcher={"command": "tskill "},  # 带空格
            context_matcher={},
            level=SafeLevel.BLACKLIST,
        ),
        Rule(
            rule_id="core_deny_stop_process",
            tool_id_pattern="bash",
            args_matcher={"command": "Stop-Process"},
            context_matcher={},
            level=SafeLevel.BLACKLIST,
        ),
        Rule(
            rule_id="core_deny_kill_process",
            tool_id_pattern="bash",
            args_matcher={"command": "kill -"},  # 匹配 "kill -9" / "kill -TERM"; 不匹配 "skill"
            context_matcher={},
            level=SafeLevel.BLACKLIST,
        ),
        # 裸 "kill <pid>" 单独靠rule难以区分 "skill" (子串), downgrade由 bash 自保护fallback
        # (bash._check_self_protection 精确检测对 zall PID 的 kill operation)。
        Rule(
            rule_id="core_deny_pkill",
            tool_id_pattern="bash",
            args_matcher={"command": "pkill "},  # 带空格
            context_matcher={},
            level=SafeLevel.BLACKLIST,
        ),
        Rule(
            rule_id="core_deny_killall",
            tool_id_pattern="bash",
            args_matcher={"command": "killall "},  # 带空格
            context_matcher={},
            level=SafeLevel.BLACKLIST,
        ),
        Rule(
            rule_id="core_deny_sc_stop",
            tool_id_pattern="bash",
            args_matcher={"command": "sc stop"},
            context_matcher={},
            level=SafeLevel.BLACKLIST,
        ),
        Rule(
            rule_id="core_deny_net_stop",
            tool_id_pattern="bash",
            args_matcher={"command": "net stop"},
            context_matcher={},
            level=SafeLevel.BLACKLIST,
        ),
        Rule(
            rule_id="core_deny_wmic_delete",
            tool_id_pattern="bash",
            args_matcher={"command": "wmic process"},  # "wmic process where..." 删除进程
            context_matcher={},
            level=SafeLevel.BLACKLIST,
        ),
        # v0.0.26: Windows 更多危险command
        Rule(
            rule_id="core_deny_reg_delete",
            tool_id_pattern="bash",
            args_matcher={"command": "reg delete"},
            context_matcher={},
            level=SafeLevel.BLACKLIST,
        ),
        Rule(
            rule_id="core_deny_diskpart",
            tool_id_pattern="bash",
            args_matcher={"command": "diskpart"},
            context_matcher={},
            level=SafeLevel.BLACKLIST,
        ),
        Rule(
            rule_id="core_deny_takeown",
            tool_id_pattern="bash",
            args_matcher={"command": "takeown "},
            context_matcher={},
            level=SafeLevel.BLACKLIST,
        ),
        Rule(
            rule_id="core_deny_icacls_reset",
            tool_id_pattern="bash",
            args_matcher={"command": "icacls "},
            context_matcher={},
            level=SafeLevel.BLACKLIST,
        ),
        Rule(
            rule_id="core_deny_cipher_wipe",
            tool_id_pattern="bash",
            args_matcher={"command": "cipher /w"},
            context_matcher={},
            level=SafeLevel.BLACKLIST,
        ),
        Rule(
            rule_id="core_deny_vssadmin_delete",
            tool_id_pattern="bash",
            args_matcher={"command": "vssadmin delete"},
            context_matcher={},
            level=SafeLevel.BLACKLIST,
        ),
        Rule(
            rule_id="core_deny_wevtutil_clear",
            tool_id_pattern="bash",
            args_matcher={"command": "wevtutil cl"},
            context_matcher={},
            level=SafeLevel.BLACKLIST,
        ),
        Rule(
            rule_id="core_deny_powercfg_change",
            tool_id_pattern="bash",
            args_matcher={"command": "powercfg "},
            context_matcher={},
            level=SafeLevel.BLACKLIST,
        ),
        # v0.0.26: Unix 更多危险command
        Rule(
            rule_id="core_deny_pipe_to_shell",
            tool_id_pattern="bash",
            args_matcher={"command": "curl | sh"},
            context_matcher={},
            level=SafeLevel.BLACKLIST,
        ),
        Rule(
            rule_id="core_deny_wget_pipe_shell",
            tool_id_pattern="bash",
            args_matcher={"command": "wget | sh"},
            context_matcher={},
            level=SafeLevel.BLACKLIST,
        ),
        Rule(
            rule_id="core_deny_curl_pipe_bash",
            tool_id_pattern="bash",
            args_matcher={"command": "curl | bash"},
            context_matcher={},
            level=SafeLevel.BLACKLIST,
        ),
        Rule(
            rule_id="core_deny_iptables_flush",
            tool_id_pattern="bash",
            args_matcher={"command": "iptables"},
            context_matcher={},
            level=SafeLevel.BLACKLIST,
        ),
        Rule(
            rule_id="core_deny_ufw_change",
            tool_id_pattern="bash",
            args_matcher={"command": "ufw "},
            context_matcher={},
            level=SafeLevel.BLACKLIST,
        ),
    ]

    user_rules: list[Rule] = []
    project_rules: list[Rule] = []

    # load用户级rule
    if user_path:
        path = Path(user_path)
    else:
        path = Path.home() / ".zall" / "rules.toml"
    if path.exists():
        try:
            user_rules = _rules_from_toml(path)
        except Exception as e:
            # v0.0.6 fix (C5): parseerror不崩溃, 记录warning并继续
            import warnings
            warnings.warn(f"Failed to parse user rules from {path}: {e}")

    # load项目级rule
    if project_path:
        path = Path(project_path) / ".zall" / "rules.toml"
    else:
        path = Path.cwd() / ".zall" / "rules.toml"
    if path.exists():
        try:
            project_rules = _rules_from_toml(path)
        except Exception as e:
            import warnings
            warnings.warn(f"Failed to parse project rules from {path}: {e}")

    # Always include default safe rules for basic usability, even when
    # user or project rules exist. Custom rules are additive — they can
    # override defaults by matching the same tool_id_pattern (blacklist > whitelist
    # via priority chain in context_judge), but removing defaults entirely
    # would make all standard tools greylist, breaking the out-of-box experience.
    default_rules = _default_safe_rules()

    # v0.0.13: todo_list 是 zall 原生显示型tool (无副作用, §9.2.6),
    # default whitelist 且 **无条件**应用 (不论用户是否有自定义 rules.toml)。
    # 它是进度投影, 不应因 deny-by-default 落到 greylist 每次弹confirm。
    # 用户仍可用显式 blacklist rule覆盖 (blacklist > whitelist, 优先级链守)。
    native_allow_todo = Rule(
        rule_id="native_allow_todo",
        tool_id_pattern="todo_list",
        args_matcher={}, context_matcher={}, level=SafeLevel.WHITELIST,
    )

    # Item F: greylist_deny rule — 中等危险command, promptconfirm而非直接拦截
    greylist_deny: list[Rule] = [
        Rule(
            rule_id="greylist_deny_del_file",
            tool_id_pattern="bash",
            args_matcher={"command": "del "},
            context_matcher={},
            level=SafeLevel.GREYLIST,
            description="del (non-recursive) — 确认删除文件",
        ),
        Rule(
            rule_id="greylist_deny_rm_file",
            tool_id_pattern="bash",
            args_matcher={"command": "rm "},
            context_matcher={},
            level=SafeLevel.GREYLIST,
            description="rm (non-force) — 确认删除文件",
        ),
        Rule(
            rule_id="greylist_deny_format",
            tool_id_pattern="bash",
            args_matcher={"command": "format "},
            context_matcher={},
            level=SafeLevel.GREYLIST,
            description="format — 确认格式化",
        ),
        Rule(
            rule_id="greylist_deny_shutdown",
            tool_id_pattern="bash",
            args_matcher={"command": "shutdown "},
            context_matcher={},
            level=SafeLevel.GREYLIST,
            description="shutdown — 确认关机",
        ),
        Rule(
            rule_id="greylist_deny_taskkill",
            tool_id_pattern="bash",
            args_matcher={"command": "taskkill "},
            context_matcher={},
            level=SafeLevel.GREYLIST,
            description="taskkill — 确认终止进程",
        ),
        Rule(
            rule_id="greylist_deny_reg_delete",
            tool_id_pattern="bash",
            args_matcher={"command": "reg delete"},
            context_matcher={},
            level=SafeLevel.GREYLIST,
            description="reg delete — 确认注册表操作",
        ),
        Rule(
            rule_id="greylist_deny_diskpart",
            tool_id_pattern="bash",
            args_matcher={"command": "diskpart"},
            context_matcher={},
            level=SafeLevel.GREYLIST,
            description="diskpart — 确认磁盘操作",
        ),
        Rule(
            rule_id="greylist_deny_takeown",
            tool_id_pattern="bash",
            args_matcher={"command": "takeown "},
            context_matcher={},
            level=SafeLevel.GREYLIST,
            description="takeown — 确认所有权变更",
        ),
    ]

    return RuleSet(
        core_deny_rules=tuple(core_deny),
        user_local_rules=tuple(project_rules + user_rules + default_rules + [native_allow_todo]),
        greylist_deny_rules=tuple(greylist_deny),  # Item F
    )


def _rules_from_toml(path: Path) -> list[Rule]:
    """从 TOML fileparse rules。"""
    data = _load_toml_simple(path)
    rules: list[Rule] = []
    for entry in data.get("rules", []):
        rid = entry.get("id", f"rule_{len(rules)}")
        tool_id = entry.get("tool_id", "*")
        args = entry.get("args", {})
        ctx = entry.get("context", {})
        level = _parse_level(entry.get("level", "greylist"))
        rules.append(
            Rule(
                rule_id=rid,
                tool_id_pattern=tool_id,
                args_matcher=args,
                context_matcher=ctx,
                level=level,
            )
        )
    return rules