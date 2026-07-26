"""zall._util.string — 字符串operation共享toolfunction。

G11 (kimi utils/string.py 对标, cell-width 增强):
  shorten / shorten_middle 是终端 cell 宽度感知的截断 — CJK 全角字符计 2 格,
  替代各处 `text[:N] + "..."` (对 CJK 双宽不感知, rich 表格会溢出)。

IPR constraints:
  IPR-0: tests/test_shorten_invariants.py (含反例)
  IPR-3: 纯 stdlib
"""

from __future__ import annotations

import re
import unicodedata

_NEWLINE_RE = re.compile(r"[\r\n]+")


def display_width(text: str) -> int:
    """终端 cell 宽度: East Asian Wide/Fullwidth 计 2, 其余计 1。"""
    return sum(2 if unicodedata.east_asian_width(ch) in "FW" else 1 for ch in text)


def _cut_to_cells(text: str, budget: int) -> int:
    """返回使 text[:i] 的 cell 宽 <= budget 的最大 i。"""
    acc = 0
    for i, ch in enumerate(text):
        w = 2 if unicodedata.east_asian_width(ch) in "FW" else 1
        if acc + w > budget:
            return i
        acc += w
    return len(text)


def shorten(text: str, *, width: int, placeholder: str = "…") -> str:
    """截断到至多 width 个终端 cell。

    空白归一化后截断 — 切点附近有词边界则优先词边界,
    CJK 无空格时硬切 (不塌缩成只剩省略号)。
    """
    text = " ".join(text.split())
    if display_width(text) <= width:
        return text
    budget = width - display_width(placeholder)
    if budget <= 0:
        return text[: _cut_to_cells(text, width)]
    cut = _cut_to_cells(text, budget)
    space = text.rfind(" ", 0, cut + 1)
    if space > 0:
        cut = space
    return text[:cut].rstrip() + placeholder


def truncate(text: str, width: int, placeholder: str = "…") -> str:
    """尾部 cell 截断, 不做空白归一化 (保留代码/日志行缩进)。"""
    if display_width(text) <= width:
        return text
    budget = width - display_width(placeholder)
    if budget <= 0:
        return text[: _cut_to_cells(text, width)]
    return text[: _cut_to_cells(text, budget)] + placeholder


def shorten_middle(text: str, width: int, remove_newline: bool = True) -> str:
    """中部省略截断 (路径/长 id 类: 头尾都有信息量)。cell 宽度感知。"""
    if remove_newline:
        text = _NEWLINE_RE.sub(" ", text)
    if display_width(text) <= width:
        return text
    placeholder = "…"
    budget = max(width - display_width(placeholder), 2)
    head_budget = budget // 2
    tail_budget = budget - head_budget
    head = text[: _cut_to_cells(text, head_budget)]
    # 尾部: 从反转串量出后还原
    rev = text[::-1]
    tail = rev[: _cut_to_cells(rev, tail_budget)][::-1]
    return head + placeholder + tail


def unquote(s: str) -> str:
    """TOML 风格去引号 + 剥离行内注释。

    B24: 统一 skills/loader.py 和 mcp/config.py 的 _unquote 实现。
    使用更完善的版本 (loader.py 版, 支持 " 和 ' 引号 + 行内 # 注释)。
    """
    s = s.strip()
    if s and s[0] in ('"', "'"):
        quote = s[0]
        end = s.find(quote, 1)
        if end != -1:
            return s[1:end]
    # 非引号 / 未闭合: 按首个 # 剥离行内注释
    h = s.find("#")
    if h != -1:
        s = s[:h]
    return s.strip()