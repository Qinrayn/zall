"""Tests for self-evolution closed loop (E2).

Verifies that apply_suggestion has real side effects:
  - create_skill writes a .md file to the skills directory
  - adjust_judge writes to learn_overrides.json
  - Counterexample: without apply_suggestion, no files are created.

Corresponds to:
  MASTER.md §12.3 E2 (Self-evolution: 建议闭环)
  auto_learn.py apply_suggestion() (E2.1/E2.2)

IPR-0: each test includes a counterexample.
"""

from __future__ import annotations

import json
import os

import pytest

from zall.core.lifecycle import SelfSuggestion


class TestSelfEvolutionClosedLoop:
    """Self-evolution closed-loop invariant tests."""

    # ── Fixtures ──

    @pytest.fixture
    def skills_dir(self, tmp_path):
        """Return a temporary skills directory path."""
        d = tmp_path / "skills"
        d.mkdir(parents=True, exist_ok=True)
        return str(d)

    @pytest.fixture
    def overrides_path(self, tmp_path):
        """Return a temporary learn_overrides.json path."""
        return str(tmp_path / "learn_overrides.json")

    @pytest.fixture
    def ext(self, skills_dir, overrides_path, tmp_path):
        """Create an AutoLearnExtension with temp paths for all persistence."""
        from zall.extensions.auto_learn import AutoLearnExtension

        learned_path = str(tmp_path / "learned" / "auto_learn.jsonl")
        return AutoLearnExtension(
            learned_path=learned_path,
            skills_dir=skills_dir,
            learn_overrides_path=overrides_path,
        )

    # ── E2.1: create_skill writes file ──

    def test_create_skill_writes_file(self, ext, skills_dir):
        """apply_suggestion(kind='create_skill') must write a real .md file."""
        suggestion = SelfSuggestion(
            kind="create_skill",
            target="bash_read_grep",
            value="bash -> read_file -> grep",
            confidence=0.8,
            evidence="Tool chain observed 3x. Create a reusable skill.",
        )
        result = ext.apply_suggestion(suggestion)

        assert result["applied"] is True
        assert result["kind"] == "create_skill"
        # Check the file exists
        filepath = os.path.join(skills_dir, "bash_read_grep.md")
        assert os.path.exists(filepath), f"Skill file {filepath} was not created"
        # Check content is meaningful
        content = open(filepath, encoding="utf-8").read()
        assert "bash_read_grep" in content
        assert "bash -> read_file -> grep" in content
        assert "Tool chain observed 3x" in content

    def test_create_skill_file_has_content(self, ext, skills_dir):
        """The written skill file must contain the prompt and evidence."""
        suggestion = SelfSuggestion(
            kind="create_skill",
            target="my_skill",
            value="read_file -> edit_file",
            confidence=0.9,
            evidence="Frequent edit-after-read pattern.",
        )
        ext.apply_suggestion(suggestion)
        filepath = os.path.join(skills_dir, "my_skill.md")
        content = open(filepath, encoding="utf-8").read()
        assert "read_file -> edit_file" in content
        assert "Frequent edit-after-read pattern." in content

    # ── E2.2: adjust_judge writes overrides ──

    def test_adjust_judge_writes_overrides(self, ext, overrides_path):
        """apply_suggestion(kind='adjust_judge') must write learn_overrides.json."""
        suggestion = SelfSuggestion(
            kind="adjust_judge",
            target="bugfix",
            value="system",
            confidence=0.7,
            evidence="Bugfix GoalType has high error rate. Switch to system Judge.",
        )
        result = ext.apply_suggestion(suggestion)

        assert result["applied"] is True
        assert result["kind"] == "adjust_judge"
        assert "next run" in result["message"]
        # Check the file exists
        assert os.path.exists(overrides_path), "learn_overrides.json was not created"
        # Check content is parseable and correct
        with open(overrides_path, encoding="utf-8") as f:
            data = json.load(f)
        assert data.get("judge_bugfix") == "system"

    def test_adjust_judge_merges_existing_overrides(self, ext, overrides_path):
        """Multiple adjust_judge calls must merge, not overwrite."""
        # First override
        ext.apply_suggestion(SelfSuggestion(
            kind="adjust_judge", target="bugfix", value="system",
            confidence=0.7, evidence="",
        ))
        # Second override (different target)
        ext.apply_suggestion(SelfSuggestion(
            kind="adjust_judge", target="feature", value="llm",
            confidence=0.7, evidence="",
        ))
        with open(overrides_path, encoding="utf-8") as f:
            data = json.load(f)
        assert data.get("judge_bugfix") == "system"
        assert data.get("judge_feature") == "llm"

    # ── E2.3: get_config_overrides reads persisted overrides ──

    def test_get_config_overrides_reads_persisted_judge(self, ext, overrides_path):
        """get_config_overrides must include judge overrides from learn_overrides.json."""
        # Apply a judge override (writes to file)
        ext.apply_suggestion(SelfSuggestion(
            kind="adjust_judge", target="research", value="system",
            confidence=0.7, evidence="",
        ))
        # get_config_overrides should read it back
        config = ext.get_config_overrides()
        k_overrides = config.get("k_overrides", {})
        assert k_overrides.get("judge_research") == "system"

    # ── Counterexample: no apply_suggestion = no files ──

    def test_counterexample_no_apply_no_change(self, skills_dir, overrides_path):
        """Without apply_suggestion, no skill files or override files are created."""
        from zall.extensions.auto_learn import AutoLearnExtension

        # Create extension but do NOT call apply_suggestion
        ext = AutoLearnExtension(
            learned_path=os.path.join(os.path.dirname(skills_dir), "learned", "auto_learn.jsonl"),
            skills_dir=skills_dir,
            learn_overrides_path=overrides_path,
        )
        # Generate a suggestion (simulate pattern detection)
        inp = self._make_turn_done_input()
        _ = ext.on_turn_done(inp)
        # Check that no files were created
        skill_file = os.path.join(skills_dir, "bash_read_grep.md")
        assert not os.path.exists(skill_file), "Skill file created without apply_suggestion"
        assert not os.path.exists(overrides_path), (
            "learn_overrides.json created without apply_suggestion"
        )

    # ── Helper to create a minimal TurnDoneInput for counterexample ──

    def _make_turn_done_input(self):
        """Create a minimal TurnDoneInput for testing."""
        from zall.core.lifecycle import TurnDoneInput

        # Create a minimal egress-like object
        class _MinimalEgress:
            success = True
            step_count = 5

        return TurnDoneInput(
            egress=_MinimalEgress(),
            step_count=5,
            tool_counts={"bash": 5, "read_file": 3, "grep": 2},
            tool_errors={"bash": 2},
            goal_type="bugfix",
        )