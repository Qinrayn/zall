"""ANSI-16 语法主题 (G7, kimi utils/rich/syntax.py 对标).

痛点: pygments 具名主题 (one-dark/nord) 是 truecolor 固定色板 + 固定背景,
浅色终端下不可读。ANSI-16 主题把 token 映射到终端的 16 个语义色,
自动跟随用户终端配色 (浅色终端自动可读)。

用法:
  - Theme.code_theme = "zall-ansi" 时, 消费方经 `resolve_code_theme()`
    得到 ANSISyntaxTheme 实例; 其他值 (one-dark/nord/...) 原样透传。
  - Theme.code_bg = "" 时, `resolve_code_bg()` 返回 None (跟随终端背景)。

IPR constraints:
  IPR-0: tests/test_syntax_theme_invariants.py (含反例)
  IPR-3: 渲染层, rich + pygments (rich 的传递依赖) only
"""

from __future__ import annotations

from pygments.token import (
    Comment,
    Generic,
    Keyword,
    Name,
    Number,
    Operator,
    Punctuation,
    String,
)
from pygments.token import Literal as PygLiteral
from pygments.token import Text as PygText
from pygments.token import Token as PygToken
from rich.style import Style
from rich.syntax import ANSISyntaxTheme, SyntaxTheme

ANSI_THEME_NAME = "zall-ansi"

# kimi KIMI_ANSI_THEME token 映射移植: 只用 16 色语义名 + default,
# 关键字 magenta / 字符串 bright_blue / 函数 bright_cyan / 类 bright_yellow。
ZALL_ANSI_THEME = ANSISyntaxTheme({
    PygToken: Style(color="default"),
    PygText: Style(color="default"),
    Comment: Style(color="bright_black", italic=True),
    Keyword: Style(color="magenta"),
    Keyword.Constant: Style(color="cyan"),
    Keyword.Declaration: Style(color="magenta"),
    Keyword.Namespace: Style(color="magenta"),
    Keyword.Pseudo: Style(color="magenta"),
    Keyword.Reserved: Style(color="magenta"),
    Keyword.Type: Style(color="magenta"),
    Name: Style(color="default"),
    Name.Attribute: Style(color="cyan"),
    Name.Builtin: Style(color="bright_yellow"),
    Name.Builtin.Pseudo: Style(color="cyan"),
    Name.Class: Style(color="bright_yellow", bold=True),
    Name.Constant: Style(color="cyan"),
    Name.Decorator: Style(color="bright_cyan"),
    Name.Entity: Style(color="bright_yellow"),
    Name.Exception: Style(color="bright_yellow", bold=True),
    Name.Function: Style(color="bright_cyan"),
    Name.Label: Style(color="cyan"),
    Name.Namespace: Style(color="magenta"),
    Name.Other: Style(color="bright_cyan"),
    Name.Property: Style(color="cyan"),
    Name.Tag: Style(color="bright_green"),
    Name.Variable: Style(color="bright_yellow"),
    PygLiteral: Style(color="bright_blue"),
    PygLiteral.Date: Style(color="bright_blue"),
    String: Style(color="bright_blue"),
    String.Doc: Style(color="bright_blue", italic=True),
    String.Interpol: Style(color="bright_blue"),
    String.Affix: Style(color="cyan"),
    Number: Style(color="cyan"),
    Operator: Style(color="default"),
    Operator.Word: Style(color="magenta"),
    Punctuation: Style(color="default"),
    Generic.Deleted: Style(color="red"),
    Generic.Emph: Style(italic=True),
    Generic.Error: Style(color="bright_red", bold=True),
    Generic.Heading: Style(color="cyan", bold=True),
    Generic.Inserted: Style(color="green"),
    Generic.Output: Style(color="bright_black"),
    Generic.Prompt: Style(color="bright_cyan"),
    Generic.Strong: Style(bold=True),
    Generic.Subheading: Style(color="cyan"),
    Generic.Traceback: Style(color="bright_red", bold=True),
})


def resolve_code_theme(theme: str | SyntaxTheme) -> str | SyntaxTheme:
    """"zall-ansi" → ANSISyntaxTheme 实例; 其余原样透传 (pygments 具名主题)。"""
    if isinstance(theme, str) and theme.lower() == ANSI_THEME_NAME:
        return ZALL_ANSI_THEME
    return theme


def resolve_code_bg(bg: str) -> str | None:
    """空串 → None (跟随终端背景, ANSI 主题必需); 否则原样。"""
    return bg or None


__all__ = ["ANSI_THEME_NAME", "ZALL_ANSI_THEME", "resolve_code_theme", "resolve_code_bg"]

