"""zall.skills — 可复用工作stream (§9.2.7 斜杠command / §9.4 skills)。

skill = 预填的 Goal 模板 (可复用 prompt), 通过 /skill <name> [args] 调用。
调用 skill = 展开为 task 文本 → 走完整 Goal 锁定 + ConfirmGate (不是免确认的宏)。
"""

from __future__ import annotations

from zall.skills.loader import Skill, find_skill, load_skills

__all__ = ["Skill", "find_skill", "load_skills"]
