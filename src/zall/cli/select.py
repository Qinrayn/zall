"""zall.cli.select — 可复用选择器 (REPL 数字键 + 纯逻辑)。

供确认门 / /model 选型 / 会话选择器复用。TUI 用 widgets.SelectMenu (方向键 + 数字键);
本模块给 --no-tui / 一次性 / REPL 路径用 input() 数字选择: 跨平台稳、无需 raw 模式。

设计: parse_selection / render_choices 为纯函数 (可离线单测); select_prompt 注入
input_fn 便于测试。空/非法/越界输入 → default_index (安全默认)。IPR-3: stdlib only。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

# 一个选项: (value, label, desc)。value 是回传给调用方的语义值 (如确认门的 'y'/'n')。
Choice = tuple[str, str, str]


def parse_selection(raw: str, n: int, *, default_index: int = 0) -> int:
    """把用户输入解析成选项下标 [0, n)。

    规则: 空 → default; 纯数字且在 1..n → 对应下标; 其它 (非法/越界) → default。
    n<=0 → -1。default 越界时回退 0。
    """
    if n <= 0:
        return -1
    default = default_index if 0 <= default_index < n else 0
    s = (raw or "").strip()
    if not s:
        return default
    if s.isdigit():
        i = int(s) - 1  # 1-based 展示 → 0-based 下标
        if 0 <= i < n:
            return i
    return default


def render_choices(
    title: str, choices: Sequence[Choice], *, default_index: int = 0,
) -> str:
    """渲染编号选项为纯文本 (无 emoji, 便于测试与非 TTY 输出)。"""
    lines: list[str] = []
    if title:
        lines.append(f"  {title}")
    for i, (_value, label, desc) in enumerate(choices):
        mark = "*" if i == default_index else " "
        line = f"   {mark}{i + 1}. {label}"
        if desc:
            line += f"  -  {desc}"
        lines.append(line)
    return "\n".join(lines)


def select_prompt(
    out: Any,
    title: str,
    choices: Sequence[Choice],
    *,
    input_fn: Callable[[str], str] = input,
    default_index: int = 0,
) -> str:
    """REPL 数字选择器: 显示编号选项, 读入选择, 返回选中项的 value。

    空/非法 → default_index; 非交互 (EOF/中断) → default。choices 为空 → 空串。
    """
    if not choices:
        return ""
    default = default_index if 0 <= default_index < len(choices) else 0
    out.write(render_choices(title, choices, default_index=default) + "\n")
    if hasattr(out, "flush"):
        try:
            out.flush()
        except Exception:
            pass
    try:
        raw = input_fn(f"  select [1-{len(choices)}] (Enter={default + 1}): ")
    except (EOFError, KeyboardInterrupt):
        raw = ""
    idx = parse_selection(raw, len(choices), default_index=default)
    return choices[idx][0]
