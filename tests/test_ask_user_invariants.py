"""ask_user 结构化问卷工具 (kimi AskUserQuestion 对标) 不变量测试.

IPR-0: each test must contain a counterexample.

Protected invariants:
  I-ASKUSER-1: 未注入交互 (无人在场) → 自动 dismiss, 返回"自行决策"JSON,
               绝不阻塞 (kimi afk 对标)。
  I-ASKUSER-2: 已注入交互 → 逐题弹选择器, 答案为选中 label 的 JSON;
               系统自动补 "Other" 项, 选中后走自由文本输入。
  I-ASKUSER-3: 兜底校验 — 空问题/选项数越界/缺 label → 报错不弹面板 (反例)。
  I-ASKUSER-4: 子代理工具集必须排除 ask_user (root-only, 反例孪生:
               spawn_subagent 也仍被排除)。
"""

from __future__ import annotations

import json

import pytest

from zall.tools.ask_user import AskUserTool


def _one_question(**over):
    q = {
        "question": "Which library should we use?",
        "header": "Library",
        "options": [
            {"label": "httpx (Recommended)", "description": "async support"},
            {"label": "requests", "description": "simplest"},
        ],
    }
    q.update(over)
    return {"questions": [q]}


# ── I-ASKUSER-1: 无人在场自动 dismiss ──


def test_auto_dismiss_without_interaction() -> None:
    tool = AskUserTool()
    result = tool.execute(_one_question())
    assert result.success
    data = json.loads(result.output)
    assert data["answers"] == {}
    assert "Make your own" in data["note"]


# ── I-ASKUSER-2: 交互路径 ──


def test_choose_returns_selected_label() -> None:
    tool = AskUserTool()
    seen_titles: list[str] = []

    def _choose(title, choices):
        seen_titles.append(title)
        # 反例孪生: 系统必须已自动补 Other 项
        assert choices[-1][0] == "__other__"
        assert len(choices) == 3                 # 2 选项 + Other
        return choices[0][0]                     # 选第一项

    tool.set_interaction(choose_fn=_choose)
    result = tool.execute(_one_question())
    assert result.success
    data = json.loads(result.output)
    assert data["answers"]["Which library should we use?"] == "httpx (Recommended)"
    assert seen_titles == ["[Library] Which library should we use?"]


def test_other_option_routes_to_free_text() -> None:
    tool = AskUserTool()
    tool.set_interaction(
        choose_fn=lambda title, choices: "__other__",
        text_fn=lambda prompt: "  use urllib3 directly  ",
    )
    result = tool.execute(_one_question())
    data = json.loads(result.output)
    assert data["answers"]["Which library should we use?"] == "use urllib3 directly"


def test_multiple_questions_all_answered() -> None:
    tool = AskUserTool()
    picks = iter(["opt_1", "opt_0"])
    tool.set_interaction(choose_fn=lambda t, c: next(picks))
    args = {"questions": [
        _one_question()["questions"][0],
        {"question": "Deploy target?", "options": [
            {"label": "docker"}, {"label": "bare metal"}]},
    ]}
    data = json.loads(tool.execute(args).output)
    assert data["answers"]["Which library should we use?"] == "requests"
    assert data["answers"]["Deploy target?"] == "docker"


def test_interaction_failure_returns_error() -> None:
    """反例: 交互层异常 → 工具报错而非崩溃/挂起。"""
    tool = AskUserTool()

    def _boom(title, choices):
        raise RuntimeError("panel crashed")

    tool.set_interaction(choose_fn=_boom)
    result = tool.execute(_one_question())
    assert not result.success
    assert "interaction failed" in result.output


# ── I-ASKUSER-3: 兜底校验反例 ──


def test_validation_counterexamples() -> None:
    tool = AskUserTool()
    tool.set_interaction(choose_fn=lambda t, c: pytest.fail("must not prompt"))
    # 空 questions
    assert not tool.execute({"questions": []}).success
    # 缺问题文本
    bad = _one_question(question="   ")
    assert not tool.execute(bad).success
    # 选项过少
    bad2 = _one_question(options=[{"label": "only one"}])
    assert not tool.execute(bad2).success
    # 选项过多
    bad3 = _one_question(options=[{"label": f"o{i}"} for i in range(5)])
    assert not tool.execute(bad3).success
    # 缺 label
    bad4 = _one_question(options=[{"label": "a"}, {"description": "no label"}])
    assert not tool.execute(bad4).success
    # 问题数超限
    q = _one_question()["questions"][0]
    assert not tool.execute({"questions": [q, q, q, q]}).success


# ── I-ASKUSER-4: 子代理排除 (root-only) ──


def test_subagent_toolset_excludes_ask_user() -> None:
    from zall.core.tool import ToolRegistry
    from zall.tools.spawn_subagent import _build_subagent_tools

    class _Stub:
        def __init__(self, tid): self.tool_id = tid
        schema = {}

        def execute(self, args): ...

    parent = ToolRegistry(tools=(
        _Stub("read_file"), _Stub("ask_user"), _Stub("spawn_subagent"),
    ))
    sub = _build_subagent_tools(parent)
    ids = {t.tool_id for t in sub.tools}
    assert "ask_user" not in ids                 # root-only
    assert "spawn_subagent" not in ids           # 反例孪生: 防递归排除仍在
    assert "read_file" in ids                    # 其余工具正常继承


def test_tool_registered_in_native_set() -> None:
    from zall.cli.orchestrator import build_tools, clear_native_tools_cache
    clear_native_tools_cache()
    try:
        reg = build_tools()
        assert reg.has("ask_user")
    finally:
        clear_native_tools_cache()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
