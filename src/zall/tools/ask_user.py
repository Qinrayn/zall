"""zall.tools.ask_user — 结构化问用户工具 (kimi AskUserQuestion 对标, 原创).

kimi tools/ask_user 逐行研读后按 zall 同步架构重写。核心价值:
把"模型问用户"从纯文本 STOP (用户要自己组织回答) 升级为**结构化选择题**:
1-3 个问题、每题 2-4 个互斥选项 (label + 权衡说明)、系统自动补 "Other"
自由输入项 — 用户按数字就能回答, 模型拿到机器可解析的 JSON 答案。

kimi 巧思保留:
  - schema 强约束 (问题数/选项数上下限, 选项须含权衡描述, 推荐项标注)
  - 自动补 "Other" (模型不必自己想兜底项)
  - 无人在场 (afk / 非交互 / 未注入交互) → 自动 dismiss 返回
    "make your own decision" — 模型不浪费回合等一个不存在的人。

交互注入 (与 spawn_subagent.set_context 同款模式):
  set_interaction(choose_fn, text_fn) 由 CLI 层构建时注入 —
  REPL → cli.select.select_prompt; TUI → TuiUserResponder 选择菜单桥。

IPR constraints:
  IPR-0: tests/test_ask_user_invariants.py (含反例)
  IPR-3: stdlib only
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from zall.core.tool import ToolResult
from zall.core.tool_kind import ToolKind

if TYPE_CHECKING:
    from zall.core.tool import ToolCapabilities

MAX_QUESTIONS = 3
_OTHER_VALUE = "__other__"

_AUTO_DISMISS_OUTPUT = json.dumps({
    "answers": {},
    "note": ("No user is present to answer (non-interactive run). "
             "Make your own best judgment and proceed."),
})


class AskUserTool:
    """ask_user — 结构化问卷 (选择题 + Other 自由输入)。"""

    __test__ = False

    def __init__(self) -> None:
        # choose_fn(title, choices[(value,label,desc)]) -> value | None
        self._choose_fn: Callable[[str, list[tuple[str, str, str]]], str | None] | None = None
        # text_fn(prompt) -> str  (Other 自由输入)
        self._text_fn: Callable[[str], str] | None = None

    def set_interaction(
        self,
        choose_fn: Callable[[str, list[tuple[str, str, str]]], str | None],
        text_fn: Callable[[str], str] | None = None,
    ) -> None:
        """注入交互实现 (REPL/TUI 构建时调用; 未注入 → 自动 dismiss)。"""
        self._choose_fn = choose_fn
        self._text_fn = text_fn

    @property
    def tool_id(self) -> str:
        return "ask_user"

    @property
    def kind(self) -> ToolKind:
        return ToolKind.READ

    @property
    def capabilities(self) -> ToolCapabilities:
        from zall.core.tool import ToolCapabilities, ToolScope
        return ToolCapabilities(is_read_only=True, tool_scope=ToolScope.Read)

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "ask_user",
                "description": (
                    "Ask the user one or more structured multiple-choice "
                    "questions when a decision genuinely changes your next "
                    "action (library choice, destructive-action confirmation, "
                    "ambiguous requirements). Do NOT use for routine progress "
                    "check-ins. The system automatically adds an 'Other' "
                    "free-text option — do not add one yourself. If no user is "
                    "present the questions are auto-dismissed and you must "
                    "decide on your own. Returns JSON: "
                    '{"answers": {question: chosen_label}}.'
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "questions": {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": MAX_QUESTIONS,
                            "description": f"1-{MAX_QUESTIONS} specific, actionable questions.",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "question": {
                                        "type": "string",
                                        "description": "A specific question ending with '?'.",
                                    },
                                    "header": {
                                        "type": "string",
                                        "description": "Short category tag (max 12 chars, e.g. 'Library').",
                                    },
                                    "options": {
                                        "type": "array",
                                        "minItems": 2,
                                        "maxItems": 4,
                                        "description": (
                                            "2-4 distinct, mutually exclusive options. "
                                            "Put the recommended one first with "
                                            "'(Recommended)' appended to its label."
                                        ),
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "label": {
                                                    "type": "string",
                                                    "description": "Concise display text (1-5 words).",
                                                },
                                                "description": {
                                                    "type": "string",
                                                    "description": "Trade-offs of choosing this option.",
                                                },
                                            },
                                            "required": ["label"],
                                        },
                                    },
                                },
                                "required": ["question", "options"],
                            },
                        },
                    },
                    "required": ["questions"],
                },
            },
        }

    # ── 执行 ──

    def execute(self, args: dict[str, Any]) -> ToolResult:
        questions = args.get("questions")
        err = self._validate(questions)
        if err:
            return ToolResult(success=False, output=f"[ask_user invalid: {err}]", error=err)
        questions = questions or []

        # 无人在场 → 自动 dismiss (kimi afk 对标)
        if self._choose_fn is None:
            return ToolResult(success=True, output=_AUTO_DISMISS_OUTPUT)

        answers: dict[str, str] = {}
        for q in questions:
            text = str(q.get("question", "")).strip()
            header = str(q.get("header", "")).strip()
            title = f"[{header}] {text}" if header else text
            choices: list[tuple[str, str, str]] = []
            for i, opt in enumerate(q.get("options", [])):
                label = str(opt.get("label", "")).strip()
                desc = str(opt.get("description", "")).strip()
                choices.append((f"opt_{i}", label, desc))
            # kimi 对标: 系统自动补 Other 自由输入项
            choices.append((_OTHER_VALUE, "Other (type your own)", ""))

            try:
                picked = self._choose_fn(title, choices)
            except Exception as e:
                return ToolResult(
                    success=False,
                    output=f"[ask_user interaction failed: {e}]",
                    error=str(e),
                )
            if picked == _OTHER_VALUE and self._text_fn is not None:
                try:
                    free = (self._text_fn("  your answer > ") or "").strip()
                except Exception:
                    free = ""
                answers[text] = free or "(no answer)"
            else:
                label_by_value = {v: label for v, label, _ in choices}
                answers[text] = label_by_value.get(str(picked), "(no answer)")

        return ToolResult(
            success=True,
            output=json.dumps({"answers": answers}, ensure_ascii=False),
            artifacts={"question_count": len(questions)},
        )

    @staticmethod
    def _validate(questions: Any) -> str | None:
        """schema 之外的兜底校验 (弱模型可能绕过 JSON schema)。"""
        if not isinstance(questions, list) or not questions:
            return "questions must be a non-empty array"
        if len(questions) > MAX_QUESTIONS:
            return f"at most {MAX_QUESTIONS} questions per call"
        for q in questions:
            if not isinstance(q, dict) or not str(q.get("question", "")).strip():
                return "each question needs non-empty 'question' text"
            opts = q.get("options")
            if not isinstance(opts, list) or not (2 <= len(opts) <= 4):
                return "each question needs 2-4 options"
            for opt in opts:
                if not isinstance(opt, dict) or not str(opt.get("label", "")).strip():
                    return "each option needs a non-empty 'label'"
        return None


__all__ = ["AskUserTool"]
