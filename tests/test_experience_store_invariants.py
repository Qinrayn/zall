"""ExperienceStore 不变量 (PARADIGM Step 1: 持久经验流 + 技能复利)。

不变量 (each with counterexample, IPR-0):
  I   Popperian Gate: 只有 verified 经验被 recall 为技能; unverified 不召回 (反例)。
  II  相关性: 相关任务召回重叠记录; 无关任务返回空 (反例: 不注入噪声)。
  III 跨会话复利: 新 store 实例 (= 新会话) 能加载并召回上次 verified 技能。
  IV  去重/边界: 空 task/outcome 不记录; 同 (task,outcome) 不重复。
  V   排序: 关键词重叠多的排前; 确定性 (相同库+task→相同结果)。
  + extract_keywords: 去停用词、去重、确定性。
"""

from __future__ import annotations

from zall.core.experience_store import (
    ExperienceStore,
    extract_keywords,
)


def _store(tmp_path) -> ExperienceStore:
    return ExperienceStore(path=tmp_path / "experience.jsonl")


# ──────────────────────────────────────────────────────────────────
# extract_keywords
# ──────────────────────────────────────────────────────────────────
class TestExtractKeywords:
    def test_drops_stopwords_and_dedupes(self) -> None:
        kws = extract_keywords("Write a function to parse the json config file")
        assert "parse" in kws and "json" in kws and "config" in kws
        assert "the" not in kws and "to" not in kws and "write" not in kws  # 反例: 停用词
        # 去重 + 确定性
        assert kws == extract_keywords("Write a function to parse the json config file")

    def test_empty(self) -> None:
        assert extract_keywords("") == ()


# ──────────────────────────────────────────────────────────────────
# I: Popperian Gate — 只召回 verified
# ──────────────────────────────────────────────────────────────────
class TestPopperianGate:
    def test_only_verified_recalled(self, tmp_path) -> None:
        s = _store(tmp_path)
        s.record("parse json config", "use json.loads with try/except", verified=True)
        s.record("parse yaml config", "use yaml.safe_load broken approach", verified=False)
        hits = s.recall("how to parse a json config", verified_only=True)
        assert any("json.loads" in r.outcome for r in hits)
        # 反例: 未验证的 yaml 记录即使关键词相关也不作为技能召回
        assert all(r.verified for r in hits)
        assert not any("broken approach" in r.outcome for r in hits)

    def test_skills_are_verified_only(self, tmp_path) -> None:
        s = _store(tmp_path)
        s.record("task a", "outcome a", verified=True)
        s.record("task b", "outcome b", verified=False)
        skills = s.skills()
        assert len(skills) == 1 and skills[0].outcome == "outcome a"


# ──────────────────────────────────────────────────────────────────
# II: 相关性 — 无关任务不注入噪声
# ──────────────────────────────────────────────────────────────────
class TestRelevance:
    def test_unrelated_returns_empty(self, tmp_path) -> None:
        s = _store(tmp_path)
        s.record("parse json config file", "use json.loads", verified=True)
        # 反例: 完全无关的任务 → 无召回 (不注入噪声)
        assert s.recall("brew a cup of coffee quickly") == []
        assert s.build_recall_context("brew a cup of coffee quickly") == ""

    def test_related_returns_hit(self, tmp_path) -> None:
        s = _store(tmp_path)
        s.record("parse json config file", "use json.loads carefully", verified=True)
        ctx = s.build_recall_context("need to parse a json config")
        assert "json.loads" in ctx and "LEARNED SKILLS" in ctx


# ──────────────────────────────────────────────────────────────────
# III: 跨会话复利 (新实例 = 新会话)
# ──────────────────────────────────────────────────────────────────
class TestCrossSessionCompounding:
    def test_new_session_recalls_prior_skill(self, tmp_path) -> None:
        # 会话 1: 学到并验证一个技能
        s1 = _store(tmp_path)
        s1.record("implement fibonacci function", "use iterative loop, avoid recursion depth",
                  verified=True)
        # 会话 2: 全新 store 实例 (= 新进程/新会话), 同路径
        s2 = _store(tmp_path)
        hits = s2.recall("write a fibonacci function in python")
        assert len(hits) >= 1                                  # 复利: 记住了
        assert "iterative loop" in hits[0].outcome
        # 反例: 若 verified 未持久化则召回为空 — 此断言守护持久化
        assert s2.stats()["verified"] == 1


# ──────────────────────────────────────────────────────────────────
# IV: 去重 / 边界
# ──────────────────────────────────────────────────────────────────
class TestDedupeAndBounds:
    def test_empty_not_recorded(self, tmp_path) -> None:
        s = _store(tmp_path)
        assert s.record("", "outcome", verified=True) is None       # 反例: 空 task
        assert s.record("task", "", verified=True) is None          # 反例: 空 outcome
        assert s.stats()["total"] == 0

    def test_dedupe_same_task_outcome(self, tmp_path) -> None:
        s = _store(tmp_path)
        s.record("task x", "outcome y", verified=True)
        s.record("task x", "outcome y", verified=True)              # 重复
        assert s.stats()["total"] == 1

    def test_clear(self, tmp_path) -> None:
        s = _store(tmp_path)
        s.record("t", "o", verified=True)
        s.clear()
        assert s.stats()["total"] == 0


# ──────────────────────────────────────────────────────────────────
# V: 排序 + 确定性
# ──────────────────────────────────────────────────────────────────
class TestRankingDeterminism:
    def test_higher_overlap_ranked_first(self, tmp_path) -> None:
        s = _store(tmp_path)
        s.record("parse json", "low overlap skill", verified=True)
        s.record("parse json config schema validation", "high overlap skill", verified=True)
        hits = s.recall("parse json config schema validation now", k=2)
        assert hits[0].outcome == "high overlap skill"     # 重叠多的排前
        # 确定性: 重复调用同序
        assert [h.outcome for h in hits] == [
            h.outcome for h in s.recall("parse json config schema validation now", k=2)
        ]
