"""zall.cli.tui.commit_boundary — 流式提交边界 (kimi _find_committed_boundary 对标).

kimi ui/shell/visualize/_blocks.py 的核心技巧原创重写: 超长流式回复中,
把**已稳定的前缀**提前提交进滚动历史 (scrollback), 活跃区只保留尾部 —
活跃块重渲染成本从 O(全文) 降到 O(尾部), 长回复不再越流越卡。

安全切点规则:
  - 只在段落边界 ("\\n\\n") 切, 不撕裂段落;
  - 切出前缀的 ``` 代码围栏数必须为偶 (不切断代码块, markdown 渲染不裂);
  - 前缀至少达阈值一半 (避免碎片化提交)。

IPR constraints:
  IPR-0: tests/test_commit_boundary_invariants.py (含反例)
  IPR-3: stdlib only (纯函数)
"""

from __future__ import annotations

# 活跃流式内容超过此字符数时尝试提交前缀 (阈值 = 大约一屏半文本)
DEFAULT_COMMIT_THRESHOLD = 6000


def find_committed_boundary(
    text: str, threshold: int = DEFAULT_COMMIT_THRESHOLD,
) -> int:
    """返回可安全提交进历史的前缀长度 (0 = 本次不提交)。

    调用方语义: prefix = text[:boundary] 固化进 scrollback;
    余下 text[boundary:] (调用方可 lstrip 换行) 留在活跃区继续流式。
    """
    if threshold <= 0 or len(text) <= threshold:
        return 0
    floor = threshold // 2
    cut = text.rfind("\n\n", 0, threshold)
    while cut >= floor:
        prefix = text[:cut]
        # 代码围栏成对 → 前缀是完整 markdown 片段, 可独立渲染
        if prefix.count("```") % 2 == 0:
            return cut
        cut = text.rfind("\n\n", 0, cut)
    return 0


__all__ = ["DEFAULT_COMMIT_THRESHOLD", "find_committed_boundary"]
