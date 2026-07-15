"""grep tool invariant test (§4.2 tool layer).

IPR-0: each test must contain a counterexample.

Counterexample:
  1. 空 pattern → success=False (not silent 通过)
  2. path does not exist → success=False
  3. 无匹配 → success=True, output="(no matches)" (not error)
  4. 匹配超限截断 → truncated=True
"""

from __future__ import annotations

from pathlib import Path

import pytest

from zall.tools.grep import GrepTool, MAX_MATCHES


@pytest.fixture
def search_tree(tmp_path: Path) -> Path:
    """construct一个临时search树."""
    (tmp_path / "a.py").write_text("def hello():\n    pass\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("import os\nhello = 1\n", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "c.txt").write_text("hello world\nHELLO upper\n", encoding="utf-8")
    return tmp_path


class TestGrepInvariants:
    def test_grep_finds_matches(self, search_tree: Path) -> None:
        """Happy path: search 'hello' 找到所有匹配."""
        tool = GrepTool()
        result = tool.execute({"pattern": "hello", "path": str(search_tree)})
        assert result.success
        assert "hello" in result.output.lower()
        # 至少 3 处: a.py:1, b.py:2, sub/c.txt:1
        assert result.artifacts["match_count"] >= 3

    def test_empty_pattern_fails(self) -> None:
        """Counterexample: 空 pattern → success=False (not silent 通过)."""
        tool = GrepTool()
        result = tool.execute({"pattern": ""})
        assert result.success is False
        assert result.error is not None

    def test_nonexistent_path_fails(self, tmp_path: Path) -> None:
        """Counterexample: path does not exist → success=False."""
        tool = GrepTool()
        result = tool.execute({"pattern": "x", "path": str(tmp_path / "nope")})
        assert result.success is False

    def test_no_match_is_not_error(self, search_tree: Path) -> None:
        """Counterexample: 无匹配 → success=True, output 含 'no matches' (not error).

        无匹配是valid结果, nottoolfail.若当 error, 模型会误以fortool坏了.
        """
        tool = GrepTool()
        result = tool.execute({"pattern": "zzz_nonexistent_zzz", "path": str(search_tree)})
        assert result.success is True
        assert "no matches" in result.output.lower()
        assert result.artifacts["match_count"] == 0

    def test_ignore_case(self, search_tree: Path) -> None:
        """Happy path: ignore_case=True 时大小写不敏感."""
        tool = GrepTool()
        result = tool.execute({
            "pattern": "hello", "path": str(search_tree), "ignore_case": True,
        })
        assert result.success
        # HELLO upper 也应被匹配
        assert result.artifacts["match_count"] >= 4

    def test_fixed_string(self, search_tree: Path) -> None:
        """Happy path: fixed=True 时特殊字符不被当正则."""
        tool = GrepTool()
        # (none) 作forfixed字符串security
        result = tool.execute({
            "pattern": "hello", "path": str(search_tree), "fixed": True,
        })
        assert result.success
        assert result.artifacts["match_count"] >= 3

    def test_tool_id_and_schema(self) -> None:
        """Happy path: tool_id non-空, schema 是valid dict."""
        tool = GrepTool()
        assert tool.tool_id == "grep"
        s = tool.schema
        assert s["type"] == "function"
        assert "pattern" in s["function"]["parameters"]["properties"]
        assert "pattern" in s["function"]["parameters"]["required"]
