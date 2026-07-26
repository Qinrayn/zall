"""大段粘贴折叠 (G5, kimi ui/shell/placeholders.py 对标).

痛点: 大段粘贴撑满输入区 (REPL 多行被 Enter 绑定逐行解释 / TUI TextArea
被几百行日志淹没)。折叠为 `[Pasted text #N +M lines]` 占位符, 输入区只占
一行; 提交时展开为原文发给模型。

设计 (kimi PastedTextPlaceholderHandler 的最小移植):
  - 阈值: >=1000 字符或 >=15 行 (env ZALL_PASTE_CHAR_THRESHOLD /
    ZALL_PASTE_LINE_THRESHOLD 可调)
  - 占位符可被用户当普通文本编辑/删除/移动, expand 按 token 逐个替换
  - 未知 id 的 token (跨会话历史召回) 原样保留 — 不崩、可见、可删
  - sanitize_surrogates: Windows 剪贴板可能带未配对 UTF-16 surrogate,
    直接进 json.dumps/UTF-8 文件会 UnicodeEncodeError, 入口即替换为 U+FFFD

IPR constraints:
  IPR-0: tests/test_paste_fold_invariants.py (含反例)
  IPR-3: 纯 stdlib
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

_PLACEHOLDER_RE = re.compile(r"\[Pasted text #(?P<id>\d+)(?: \+(?P<lines>\d+) lines?)?\]")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


CHAR_THRESHOLD = _env_int("ZALL_PASTE_CHAR_THRESHOLD", 1000)
LINE_THRESHOLD = _env_int("ZALL_PASTE_LINE_THRESHOLD", 15)


def sanitize_surrogates(text: str) -> str:
    """替换无法编码为 UTF-8 的孤立 surrogate (Windows 剪贴板常见)。"""
    return text.encode("utf-8", errors="surrogatepass").decode("utf-8", errors="replace")


def normalize_pasted_text(text: str) -> str:
    """CRLF/CR → LF (与 prompt_toolkit / TextArea 内部换行格式一致)。"""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def count_lines(text: str) -> int:
    if not text:
        return 1
    return text.count("\n") + 1


def should_fold(text: str) -> bool:
    return len(text) >= CHAR_THRESHOLD or count_lines(text) >= LINE_THRESHOLD


@dataclass(slots=True)
class _Entry:
    paste_id: int
    text: str


class PasteFolder:
    """一个输入会话一个实例 (REPL 的 make_prompt_fn / TUI 的 InputBar)。"""

    def __init__(self) -> None:
        self._entries: dict[int, _Entry] = {}
        self._next_id = 1

    def fold(self, text: str) -> str:
        """无条件折叠, 返回占位符 token。"""
        normalized = sanitize_surrogates(normalize_pasted_text(text))
        entry = _Entry(paste_id=self._next_id, text=normalized)
        self._entries[entry.paste_id] = entry
        self._next_id += 1
        n = count_lines(normalized)
        if n <= 1:
            return f"[Pasted text #{entry.paste_id}]"
        return f"[Pasted text #{entry.paste_id} +{n} lines]"

    def maybe_fold(self, text: str) -> str:
        """超阈值折叠为占位符; 否则返回归一化原文。"""
        normalized = sanitize_surrogates(normalize_pasted_text(text))
        if not should_fold(normalized):
            return normalized
        return self.fold(normalized)

    def expand(self, command: str) -> str:
        """占位符 → 原文 (提交/入历史时调用)。未知 id 原样保留。"""

        def _sub(m: re.Match[str]) -> str:
            entry = self._entries.get(int(m.group("id")))
            return m.group(0) if entry is None else entry.text

        return _PLACEHOLDER_RE.sub(_sub, command)

    def has_placeholder(self, text: str) -> bool:
        return _PLACEHOLDER_RE.search(text) is not None


__all__ = [
    "CHAR_THRESHOLD",
    "LINE_THRESHOLD",
    "PasteFolder",
    "count_lines",
    "normalize_pasted_text",
    "sanitize_surrogates",
    "should_fold",
]
