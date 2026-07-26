"""/lab 命令 + RedBlueLoop.on_event 观察者 不变量 (PARADIGM 落地).

把 Steps 1-3 (experience_store / sandbox_verifier / open_ended) 串成用户可跑的
/lab 命令. 这些测试**真的在沙盒里跑 Python** (fake adapter 只喂固定解, 证伪仍靠真执行).

不变量 (each with counterexample, IPR-0):
  on_event:
    A  propose + verdict 事件都被投递; verdict 载荷含 score/broken/critique。
    B  on_event 抛异常 → 循环仍完成 (反例: 若不隔离会崩)。
    C  on_event 只观察 → 报告与不带 on_event 完全一致 (确定性不受影响)。
  /lab:
    D  verified 解 → 蒸馏成技能写入经验库 (task 精确匹配)。
    E  refuted 解 (坏代码) → 什么都不蒸馏 (反例: 坏码绝不成技能)。
    F  /lab skills / stats 只读子命令正确反映经验库。
    G  无模型 → 优雅提示, 不崩。
    H  /lab (开放式) 空技能库 → 提示先 bootstrap, 不调用模型。
"""

from __future__ import annotations

import io

import zall.cli.commands.suggest as sug
from zall.core.experience_store import ExperienceStore
from zall.core.red_blue import RedBlueLoop

# ── fixtures/helpers ──

# 干净可运行 + 自带断言 (含边界) → 沙盒 exit 0 → verified
GOOD = "```python\ndef f(n):\n    return n * 2\nassert f(2) == 4\nassert f(0) == 0\nprint('ok')\n```"
# 运行即报错 → 沙盒非零退出 → refuted
BAD = "```python\nraise ValueError('nope')\n```"


class _FakeAdapter:
    """离线 adapter: complete() 恒返固定内容 (证伪仍由真实沙盒决定)。"""

    model_name = "fake-model"

    def __init__(self, content: str) -> None:
        self._content = content

    def complete(self, messages, tools, tool_choice=None):  # noqa: ANN001
        from zall.core.model import ModelResponse, StopReason
        return ModelResponse(content=self._content, stop_reason=StopReason.STOP)


def _store(tmp_path) -> ExperienceStore:
    return ExperienceStore(path=tmp_path / "experience.jsonl")


# ══════════════════════════════════════════════════════════════════
# on_event 观察者 (core)
# ══════════════════════════════════════════════════════════════════
class TestOnEvent:
    def test_propose_and_verdict_delivered(self) -> None:
        events: list[tuple[str, dict]] = []
        loop = RedBlueLoop(
            blue_fn=lambda insp: "def foo():\n    return 1",
            max_rounds=1, proposals_per_round=2,
            on_event=lambda phase, payload: events.append((phase, payload)),
        )
        loop.run("task")
        phases = [e[0] for e in events]
        assert "propose" in phases and "verdict" in phases          # A
        verdicts = [p for ph, p in events if ph == "verdict"]
        assert verdicts and all(
            {"score", "broken", "critique"} <= set(v) for v in verdicts
        )

    def test_callback_exception_isolated(self) -> None:
        def boom(phase: str, payload: dict) -> None:
            raise RuntimeError("observer blew up")

        loop = RedBlueLoop(
            blue_fn=lambda insp: "def ok():\n    return 1\n",
            max_rounds=1, proposals_per_round=1, on_event=boom,
        )
        rep = loop.run("task")           # B: 反例 — 不隔离会在此抛出
        assert rep is not None and rep.rounds == 1

    def test_observer_does_not_change_report(self) -> None:
        def blue(insp: str) -> str:
            return "def g():\n    return 42\n"

        base = RedBlueLoop(blue_fn=blue, max_rounds=2, proposals_per_round=2).run("t")
        observed = RedBlueLoop(
            blue_fn=blue, max_rounds=2, proposals_per_round=2,
            on_event=lambda p, d: None,
        ).run("t")
        # C: 观察者不影响任何报告字段 (确定性)
        assert base.round_best_scores == observed.round_best_scores
        assert base.summary() == observed.summary()


# ══════════════════════════════════════════════════════════════════
# /lab 命令 (端到端: fake adapter + 真实沙盒)
# ══════════════════════════════════════════════════════════════════
class TestLabTask:
    def test_verified_records_skill(self, tmp_path) -> None:
        store = _store(tmp_path)
        out = io.StringIO()
        r = sug.cmd_lab(
            "double a number", out, None,
            {"_experience_store": store, "_adapter": _FakeAdapter(GOOD)},
        )
        assert r == "handled"
        text = out.getvalue()
        assert "verified" in text and "skill distilled" in text     # D
        skills = store.skills()
        assert len(skills) == 1 and skills[0].task == "double a number"

    def test_refuted_records_nothing(self, tmp_path) -> None:
        store = _store(tmp_path)
        out = io.StringIO()
        sug.cmd_lab(
            "explode please", out, None,
            {"_experience_store": store, "_adapter": _FakeAdapter(BAD)},
        )
        text = out.getvalue()
        assert "refuted" in text                                    # E
        assert store.skills() == []                                 # E 反例: 坏码不成技能


class TestLabSubcommands:
    def test_skills_and_stats(self, tmp_path) -> None:
        store = _store(tmp_path)
        store.record("parse json config", "use json.loads with try/except", verified=True)
        store.record("flaky attempt", "did not survive", verified=False)

        out = io.StringIO()
        sug.cmd_lab("skills", out, None, {"_experience_store": store})
        skills_text = out.getvalue()
        assert "learned skills (1)" in skills_text and "parse json config" in skills_text  # F
        assert "flaky attempt" not in skills_text                   # F 反例: unverified 不算技能

        out2 = io.StringIO()
        sug.cmd_lab("stats", out2, None, {"_experience_store": store})
        stats_text = out2.getvalue()
        assert "verified skills: 1" in stats_text and "unverified: 1" in stats_text


class TestLabGuards:
    def test_no_model_graceful(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(sug, "_lab_get_adapter", lambda loop, state: None)
        out = io.StringIO()
        r = sug.cmd_lab("do something", out, None, {"_experience_store": _store(tmp_path)})
        assert r == "handled" and "no model available" in out.getvalue()   # G

    def test_open_ended_needs_skills(self, tmp_path) -> None:
        # 空技能库 → 开放式轮不调用模型, 提示先 bootstrap (H)
        store = _store(tmp_path)
        called = {"n": 0}

        class _Spy(_FakeAdapter):
            def complete(self, messages, tools, tool_choice=None):  # noqa: ANN001
                called["n"] += 1
                return super().complete(messages, tools, tool_choice)

        out = io.StringIO()
        sug.cmd_lab("", out, None, {"_experience_store": store, "_adapter": _Spy(GOOD)})
        assert "bootstrap" in out.getvalue()
        assert called["n"] == 0                                     # H 反例: 不该调用模型
