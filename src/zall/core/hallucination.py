"""zall.core.hallucination — PR-0 幻觉扫描 (从 loop.py 抽取, 单一职责)。

Corresponds to:
  §0  PR-0 自证伪: model stop_reason=STOP 却在文本里伪造工具输出 (bash prompt、
      文件分隔符、diff 块、HTTP 响应等) → 判定为幻觉。

设计:
  纯函数, 无状态, 预编译正则。此前内联在 AgentLoop._scan_hallucinated_content,
  为给 loop.py "god class" 瘦身 (架构评估 P0-item2) 抽取为独立模块。
  AgentLoop 保留薄委托 staticmethod 以兼容内部调用与既有测试。

IPR constraints:
  IPR-0: invariant tests at tests/test_loop_invariants.py (PR-0 scan 反例)
  IPR-3: stdlib only (re), 不 import 模型 SDK
"""

from __future__ import annotations

import re

# model 伪造工具输出的常见 pattern (预编译正则, 避免每次 STOP 都编译)。
# 与 tests/test_loop_invariants.py 的 PR-0 反例严格对应: 变更此表须同步测试。
HALLUCINATION_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\$\s+(?:sudo|apt|pip|npm|git|python|node|cd|ls|cat|cp|mv|rm|mkdir|chmod|echo)\b"), "fake_bash_prompt"),
    (re.compile(r"\w+@\w+:~[/\w]*\$"), "fake_user_host_prompt"),
    (re.compile(r"---\s*(?:BEGIN|START|END)\s*(?:FILE|CONTENT)?\s*---"), "fake_file_delimiter"),
    (re.compile(r"\b\d+\s*(?:bytes|KB|MB)\s+(?:written|read|modified|saved|created)\b"), "fake_file_size_report"),
    # Tightened: requires @@ hunk header followed by +/- lines to avoid false
    # positives on bullet points, markdown lists, and negative numbers.
    (re.compile(r"(?m)^@@.*\n[\s\S]*?^(?:\+|\-)[^+\-]"), "fake_diff_block"),
    (re.compile(r"HTTP/\d\.\d\s+\d{3}"), "fake_http_response"),
    (re.compile(r"<tool_output>"), "fake_tool_output_xml"),
    (re.compile(r"<function_call>"), "fake_function_call_xml"),
)


def scan_hallucinated_content(content: str) -> tuple[str, ...]:
    """PR-0: 扫描 STOP 回复中伪造的工具输出 pattern, 返回命中的标签元组。

    纯函数: 相同输入相同输出。无命中返回空元组 (正常回复不误报)。
    """
    if not content:
        return ()
    found: list[str] = []
    for pattern, label in HALLUCINATION_PATTERNS:
        if pattern.search(content):
            found.append(label)
    return tuple(found)
