"""zall.cli.select — 可复用选择器 (REPL 数字键 + 纯逻辑)。

供确认门 / /model 选型 / 会话选择器复用。TUI 用 widgets.SelectMenu (方向键 + 数字键);
本模块给 --no-tui / 一次性 / REPL 路径用 input() 数字选择: 跨平台稳、无需 raw 模式。

设计: parse_selection / render_choices 为纯函数 (可离线单测); select_prompt 注入
input_fn 便于测试。空/非法/越界输入 → default_index (安全默认)。IPR-3: stdlib only。
"""

from __future__ import annotations

import sys
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
) -> str | None:
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


# ──────────────────────────────────────────────────────────────────────────
# Kimi-style choice menu (↑↓ + Enter) — REPL 交互主路径 (v0.7)
# ──────────────────────────────────────────────────────────────────────────


def choice_menu(
    out: Any,
    title: str,
    choices: Sequence[Choice],
    *,
    input_fn: Callable[[str], str] | None = None,
    default_index: int = 0,
    is_tty: bool | None = None,
) -> str | None:
    """↑↓ 方向键菜单选择, 回车确认, Ctrl+C 取消 (Kimi CLI 交互口径)。

    真 TTY → prompt_toolkit Application 实时菜单 (↑/↓/j/k 移动, 数字直选,
    Enter 确认); Ctrl+C/Esc 取消 → None (不再追问); prompt_toolkit 渲染
    失败 (无终端/环境) → 降级数字选择 (select_prompt, input_fn 注入可测)。
    契约一致: 返回选中 value; 取消/EOF → None。
    """
    if not choices:
        return None
    if is_tty is None:
        is_tty = bool(hasattr(out, "isatty") and out.isatty())
    if is_tty:
        value: str | None = None
        built_ok = False
        try:
            value = _ptk_choice_menu(out, title, choices, default_index=default_index)
            built_ok = True
        except Exception:
            pass  # ptk 渲染失败 → 走数字降级, 不把失败当取消
        if built_ok:
            if value is None:
                return None  # 用户取消 (Ctrl+C/Esc) — 取消就是取消, 不追问
            label = next(
                (lbl for _v, lbl, _d in choices if _v == value),
                str(value),
            )
            out.write(f"    \u25b8 {label}\n")
            return value
        # ptk 失败 → 数字降级继续 (下面统一处理)
    if input_fn is None:
        out.write("  \u00b7 cancelled\n")
        return None
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
        out.write("  \u00b7 cancelled\n")
        return None
    idx = parse_selection(raw, len(choices), default_index=default)
    return choices[idx][0]


def _render_menu_text(
    title: str, choices: Sequence[Choice], *, current: int,
) -> list[str]:
    """渲染菜单文本行: 当前项以箭头标记 (纯函数, 供 ptk 与单测共用)。"""
    lines = [f"  {title}"]
    for i, (_value, label, desc) in enumerate(choices):
        prefix = "  \u25b8 " if i == current else "    "
        line = f"{prefix}{label}"
        if desc:
            line += f"  \u00b7  {desc}"
        lines.append(line)
    lines.append("  (\u2191\u2193/ jk 选择 \u00b7 Enter 确认 \u00b7 Ctrl+C 取消)")
    return lines


def _drain_pending_keys() -> None:
    """排空控制台残留按键 (上一菜单数字直选退出后惯按的回车等)。

    残留键会污染下一个菜单 — 第一个 Application 退出后, 紧随其后的
    Enter 停留在输入队列, 被下一个 prompt_toolkit 菜单当作自己的回车,
    直接提交第一项 (PTY 实测: 向导平台菜单被误选 Fireworks)。只在菜单
    即将读取新输入前调用; 无缓冲直接返回。
    """
    try:
        import msvcrt
        while msvcrt.kbhit():
            try:
                msvcrt.getwch()
            except Exception:
                break
    except ImportError:  # POSIX: 非阻塞读取 stdin 中已有字节
        import select
        try:
            for _ in range(256):  # 上限兜底: stdin 是持续供数的管道时不死循环
                if not select.select([sys.stdin], [], [], 0)[0]:
                    break
                sys.stdin.read(1)
        except (OSError, ValueError):
            pass
    except (OSError, ValueError):
        pass


def _ptk_choice_menu(
    out: Any, title: str, choices: Sequence[Choice], *, default_index: int = 0,
) -> str | None:
    """prompt_toolkit 方向键菜单 (Kimi Code CLI 交互骨架, 非 TUI 全屏)。

    prompt_toolkit 负责菜单区域渲染与按键; 退出时 ptk 恢复屏幕, 选中结果
    由 choice_menu 补印一行 (▸ ...) 保留于终端。内部失败会 raise,
    由 choice_menu 降级兜底。
    """
    from prompt_toolkit.application import Application
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import Layout, Window
    from prompt_toolkit.layout.controls import FormattedTextControl

    # 排空上一菜单残留按键 (数字直选后的惯按回车会污染本菜单)
    _drain_pending_keys()
    index: list[int] = [default_index if 0 <= default_index < len(choices) else 0]
    result: list[str | None] = [None]  # 结果: 选中 value / None(取消)

    def _render() -> str:
        return "\n".join(_render_menu_text(title, choices, current=index[0]))

    # 单个可变 control: 方向键只改 .text + invalidate, 不能替换对象 —
    # Window 持有的是构造时的引用, 换对象不会重绘 (移动光标失效)。
    control = FormattedTextControl(_render())

    def _refresh(app: Any) -> None:
        control.text = _render()
        app.invalidate()

    kb = KeyBindings()

    @kb.add("down")
    @kb.add("j")
    def _down(e: Any) -> None:
        index[0] = (index[0] + 1) % len(choices)
        _refresh(e.app)

    @kb.add("up")
    @kb.add("k")
    def _up(e: Any) -> None:
        index[0] = (index[0] - 1) % len(choices)
        _refresh(e.app)

    @kb.add("enter")
    def _pick(e: Any) -> None:
        result[0] = choices[index[0]][0]
        e.app.exit()

    @kb.add("c-c")
    @kb.add("escape")
    def _cancel(e: Any) -> None:
        e.app.exit()

    for digit in "123456789":
        @kb.add(digit)
        def _digit(e: Any, _d: str = digit) -> None:
            n = int(_d)
            if 1 <= n <= len(choices):
                result[0] = choices[n - 1][0]
                e.app.exit()

    app: Application[Any] = Application(
        layout=Layout(Window(control, height=len(_render().splitlines()), wrap_lines=False)),
        key_bindings=kb,
        full_screen=False,
    )
    app.run()
    return result[0]


# ──────────────────────────────────────────────────────────────────────────
# secret prompt (API key 等, 隐藏输入) — Kimi 风格
# ──────────────────────────────────────────────────────────────────────────


def _pt_secret_input(prompt_text: str) -> str | None:
    """Windows 原生隐藏输入 (msvcrt 逐键读, 不回显明文)。

    PTY 实测: 自建嵌套 prompt_toolkit Application 会与 REPL 主循环的
    PromptSession 双读同一 ConPTY 输入队列 — key 落进主 prompt buffer,
    明文打印成任务。getpass 同样走 msvcrt, 干净, 但无 Esc 取消语义;
    这里按 win_getpass 的读键回路实现, Enter 提交 / Esc 取消 / 回退键删除。
    """
    import msvcrt

    if not hasattr(msvcrt, "getwch"):
        import getpass
        return str(getpass.getpass(prompt_text) or "").strip() or None

    out_fd = sys.stdout
    try:
        out_fd.write(prompt_text)
        out_fd.flush()
    except Exception:
        pass

    chars: list[str] = []
    while True:
        try:
            ch = msvcrt.getwch()
        except (EOFError, OSError):
            return None
        if ch in "\r\n":
            break
        if ch in "\x03":
            raise KeyboardInterrupt
        if ch in "\x1b":
            return None
        if ch in ("\b", "\x7f"):
            if chars:
                chars.pop()
        elif ch == "\x00":  # 双字节高位 across two reads
            try:
                msvcrt.getwch()
            except (EOFError, OSError):
                break
        elif ch == "\xe0":  # 功能键头 — 忽略整键
            try:
                msvcrt.getwch()
            except (EOFError, OSError):
                break
        elif ord(ch) >= 32:
            chars.append(ch)
    return "".join(chars) or None


def secret_prompt(
    prompt_text: str,
    *,
    input_fn: Callable[[str], str] | None = None,
    is_tty: bool | None = None,
    out: Any = None,
) -> str | None:
    """隐藏式密钥输入 (回显掩码)。

    真终端 → 自建 Application 密码输入 (PasswordProcessor, REPL 内嵌可用);
    ptk 不可用 → getpass。注入的 input_fn (测试/脚本, 非 TTY) → 直接调用,
    明文可见 — 只在非 TTY 时放行, 否则 REPL 主输入栈的 "_input_fn"
    会让 key 明文回显。EOF/中断 → ""。
    """
    if is_tty is None:
        is_tty = bool(out is not None and hasattr(out, "isatty") and out.isatty())
    if is_tty:
        try:
            again: str | None = _pt_secret_input(prompt_text)
            return str(again if again is not None else "").strip()
        except (EOFError, KeyboardInterrupt):
            return ""
        except Exception:
            pass
        try:
            import getpass
            return str(getpass.getpass(prompt_text) or "").strip()
        except (EOFError, KeyboardInterrupt):
            return ""
    if input_fn is not None:
        try:
            return str(input_fn(prompt_text) or "").strip()
        except (EOFError, KeyboardInterrupt):
            return ""
    try:
        import getpass
        return str(getpass.getpass(prompt_text) or "").strip()
    except (EOFError, KeyboardInterrupt):
        return ""
