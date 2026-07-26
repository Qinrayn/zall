"""OpenEndedGenerator + 永续自改进轮 不变量 (PARADIGM Step 3)。

不变量 (each with counterexample):
  I   生成任务派生自 parent (非凭空)。
  II  新颖: 生成 != parent (非回声); 生成集合去重。
  III combine 需 >=2 seed; 单 seed 无 combine (反例)。
  IV  空 seed/空库 → 空生成 (诚实退让)。
  V   确定性: 相同 seed → 相同生成。
  VI  永续轮 (run_open_ended_round): 只有沙盒 verified 的解写回经验库 (Popperian Gate);
      坏解不写回 (反例)。
"""

from __future__ import annotations

from zall.core.experience_store import ExperienceStore
from zall.core.open_ended import (
    OpenEndedGenerator,
    run_open_ended_round,
)


def _store(tmp_path) -> ExperienceStore:
    return ExperienceStore(path=tmp_path / "experience.jsonl")


class TestGenerator:
    def test_derives_from_parent(self) -> None:
        g = OpenEndedGenerator()
        tasks = g.generate(k=6, seeds=["parse a json config"])
        assert tasks                                          # 有产出
        assert all("parse a json config" in t.task for t in tasks)  # I: 派生自 parent
        assert all(t.parent for t in tasks)

    def test_novel_not_echo(self) -> None:
        g = OpenEndedGenerator()
        tasks = g.generate(k=6, seeds=["implement fibonacci"])
        # II 反例: 没有一条生成任务等于原任务 (不是回声)
        assert all(t.task.strip().lower() != "implement fibonacci" for t in tasks)
        # 去重: 任务文本唯一
        texts = [t.task for t in tasks]
        assert len(texts) == len(set(texts))

    def test_combine_needs_two_seeds(self) -> None:
        g = OpenEndedGenerator()
        one = g.generate(k=10, seeds=["task alpha"])
        assert not any(t.strategy == "combine" for t in one)   # III 反例: 单 seed 无 combine
        two = g.generate(k=10, seeds=["task alpha", "task beta"])
        assert any(t.strategy == "combine" for t in two)

    def test_empty_yields_nothing(self, tmp_path) -> None:
        assert OpenEndedGenerator().generate(k=3, seeds=[]) == []      # IV
        # 空经验库 (无 verified 技能) → 空
        assert OpenEndedGenerator(store=_store(tmp_path)).generate(k=3) == []

    def test_deterministic(self) -> None:
        g = OpenEndedGenerator()
        a = g.generate(k=5, seeds=["build a cache", "parse csv"])
        b = g.generate(k=5, seeds=["build a cache", "parse csv"])
        assert [t.task for t in a] == [t.task for t in b]              # V

    def test_seeds_from_verified_skills_only(self, tmp_path) -> None:
        st = _store(tmp_path)
        st.record("verified task", "good outcome", verified=True)
        st.record("unverified task", "meh", verified=False)
        tasks = OpenEndedGenerator(store=st).generate(k=6)
        # 只从 verified 技能派生 (反例: 未验证任务不作 seed)
        assert tasks
        assert all("unverified task" not in t.task for t in tasks)
        assert any("verified task" in t.task for t in tasks)


class TestOpenEndedRound:
    def test_verified_solution_becomes_new_skill(self, tmp_path) -> None:
        st = _store(tmp_path)
        st.record("compute a sum", "sum with builtin", verified=True)  # 种子技能
        before = st.stats()["verified"]

        # blue 出能干净运行的代码 → 沙盒 verified → 写回为新技能
        rep = run_open_ended_round(lambda task: "print('generated ok')",
                                   store=st, k=2)
        assert rep.generated >= 1
        assert rep.verified >= 1                                       # VI: 有 verified
        assert st.stats()["verified"] > before                        # 技能库变厚 (复利)

    def test_broken_solution_not_recorded(self, tmp_path) -> None:
        st = _store(tmp_path)
        st.record("compute a sum", "sum with builtin", verified=True)
        before = st.stats()["verified"]

        # blue 出语法错代码 → 沙盒证伪 → 反例: 不写回经验库
        rep = run_open_ended_round(lambda task: "def (:", store=st, k=2)
        assert rep.verified == 0
        assert st.stats()["verified"] == before                        # 未污染技能库
