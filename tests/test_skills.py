"""§9.2.7 斜杠command / §9.4 skills = reusable Goal template — implementation tests (includes counterexamples, IPR-0).

covers:
  1. Skill.expand: {input} 占位符替换 / 无占位符但有参 → 末尾附加 / 原样
  2. _parse_skills: 单行 prompt / 多行 (三重引号) prompt / description
  3. load_skills: 项目级covers用户级同名 / 缺失文件 → []
  4. fail安全: 坏 TOML → [] (does not raise)
  5. find_skill: 大小写不敏感 + 前导 / 忽略
"""

from __future__ import annotations

from pathlib import Path

from zall.skills import Skill, find_skill, load_skills
from zall.skills.loader import _parse_skills


# ──────────────────────────────────────────────────────────────────────────
# 1. Skill.expand
# ──────────────────────────────────────────────────────────────────────────


class TestSkillExpand:
    def test_placeholder_filled(self) -> None:
        sk = Skill(name="explain", description="", prompt="Read {input} and explain it.")
        assert sk.expand("src/foo.py") == "Read src/foo.py and explain it."

    def test_placeholder_empty_arg(self) -> None:
        sk = Skill(name="x", description="", prompt="Read {input}.")
        assert sk.expand("") == "Read ."

    def test_no_placeholder_with_arg_appended(self) -> None:
        sk = Skill(name="review", description="", prompt="Review the current diff.")
        assert sk.expand("focus on auth.py") == (
            "Review the current diff.\n\nfocus on auth.py"
        )

    def test_no_placeholder_no_arg_asis(self) -> None:
        sk = Skill(name="review", description="", prompt="Review the current diff.")
        assert sk.expand("") == "Review the current diff."


# ──────────────────────────────────────────────────────────────────────────
# 2. _parse_skills
# ──────────────────────────────────────────────────────────────────────────


class TestParseSkills:
    def test_single_line_prompt(self) -> None:
        text = (
            '[[skills]]\n'
            'name = "review"\n'
            'description = "review diff"\n'
            'prompt = "Review the current git diff."\n'
        )
        skills = _parse_skills(text)
        assert len(skills) == 1
        assert skills[0].name == "review"
        assert skills[0].description == "review diff"
        assert skills[0].prompt == "Review the current git diff."

    def test_multiline_prompt(self) -> None:
        text = (
            '[[skills]]\n'
            'name = "review"\n'
            'description = "review diff"\n'
            'prompt = """\n'
            "Review the current git working-tree diff. Focus on:\n"
            "- correctness bugs\n"
            '- security issues\n'
            '"""\n'
        )
        skills = _parse_skills(text)
        assert len(skills) == 1
        # 多行 prompt 包裹 + 内部缩进preserve, 首尾空白由 .strip() 收掉
        assert "Review the current git working-tree diff." in skills[0].prompt
        assert "- correctness bugs" in skills[0].prompt
        assert skills[0].prompt.startswith("Review")
        assert skills[0].prompt.endswith("security issues")

    def test_multiple_skills(self) -> None:
        text = (
            '[[skills]]\nname = "a"\nprompt = "A"\n'
            '[[skills]]\nname = "b"\ndescription = "desc b"\nprompt = "B"\n'
        )
        skills = _parse_skills(text)
        assert [s.name for s in skills] == ["a", "b"]
        assert skills[1].description == "desc b"

    def test_comments_and_blank_lines_ignored(self) -> None:
        text = (
            "# a comment\n\n"
            '[[skills]]\n'
            'name = "c"  # inline-ish\n'
            'prompt = "C"\n'
        )
        skills = _parse_skills(text)
        assert len(skills) == 1
        assert skills[0].name == "c"


# ──────────────────────────────────────────────────────────────────────────
# 3. load_skills — 优先级 + 缺失
# ──────────────────────────────────────────────────────────────────────────


def _write_skill_file(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


class TestLoadSkills:
    def test_project_overrides_user_same_name(self, tmp_path: Path) -> None:
        user_file = tmp_path / "user_skills.toml"
        _write_skill_file(
            user_file,
            '[[skills]]\nname = "x"\ndescription = "user"\nprompt = "USER"\n',
        )
        proj_file = tmp_path / ".zall" / "skills.toml"
        _write_skill_file(
            proj_file,
            '[[skills]]\nname = "x"\ndescription = "proj"\nprompt = "PROJ"\n',
        )
        skills = load_skills(
            user_path=str(user_file), project_path=str(tmp_path)
        )
        assert len(skills) == 1
        assert skills[0].prompt == "PROJ"  # 项目级优先

    def test_user_and_project_distinct_names_merged(self, tmp_path: Path) -> None:
        user_file = tmp_path / "user_skills.toml"
        _write_skill_file(
            user_file,
            '[[skills]]\nname = "a"\nprompt = "A"\n',
        )
        proj_file = tmp_path / ".zall" / "skills.toml"
        _write_skill_file(
            proj_file,
            '[[skills]]\nname = "b"\nprompt = "B"\n',
        )
        skills = load_skills(
            user_path=str(user_file), project_path=str(tmp_path)
        )
        names = {s.name for s in skills}
        assert names == {"a", "b"}

    def test_missing_project_file_returns_empty(self, tmp_path: Path) -> None:
        user_file = tmp_path / "user_skills.toml"
        _write_skill_file(user_file, '[[skills]]\nname = "a"\nprompt = "A"\n')
        # 项目directory不存在 .zall/skills.toml → 只用 user
        skills = load_skills(
            user_path=str(user_file), project_path=str(tmp_path / "nope")
        )
        assert [s.name for s in skills] == ["a"]

    def test_both_missing_returns_empty(self, tmp_path: Path) -> None:
        skills = load_skills(
            user_path=str(tmp_path / "u.toml"), project_path=str(tmp_path / "p")
        )
        assert skills == []


# ──────────────────────────────────────────────────────────────────────────
# 4. failsecurity — 坏 TOML → []
# ──────────────────────────────────────────────────────────────────────────


class TestLoadSkillsFailSafe:
    def test_bad_toml_returns_empty_not_raise(self, tmp_path: Path) -> None:
        proj_file = tmp_path / ".zall" / "skills.toml"
        _write_skill_file(proj_file, "this is not valid toml @@@ [[skills]]")
        skills = load_skills(project_path=str(tmp_path))
        assert skills == []  # fail安全: 解析异常不得抛, returns []

    def test_skill_missing_prompt_skipped(self, tmp_path: Path) -> None:
        """Counterexample (IPR-0): skill 缺 prompt → 视fornon-法, 该 skill skip, 不pollution其它."""
        proj_file = tmp_path / ".zall" / "skills.toml"
        _write_skill_file(
            proj_file,
            '[[skills]]\nname = "bad"\n'  # 无 prompt
            '[[skills]]\nname = "good"\nprompt = "OK"\n',
        )
        skills = load_skills(project_path=str(tmp_path))
        assert [s.name for s in skills] == ["good"]


# ──────────────────────────────────────────────────────────────────────────
# 5. find_skill
# ──────────────────────────────────────────────────────────────────────────


class TestFindSkill:
    def test_case_insensitive(self) -> None:
        skills = [Skill(name="Review", description="", prompt="r")]
        assert find_skill(skills, "review") is not None
        assert find_skill(skills, "REVIEW") is not None

    def test_leading_slash_ignored(self) -> None:
        skills = [Skill(name="explain", description="", prompt="e")]
        assert find_skill(skills, "/explain") is not None
        assert find_skill(skills, "explain") is not None

    def test_unknown_returns_none(self) -> None:
        skills = [Skill(name="explain", description="", prompt="e")]
        assert find_skill(skills, "nope") is None
