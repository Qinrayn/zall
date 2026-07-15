"""glob tool invariant test (§4.2 tool layer).

IPR-0: each test must contain a counterexample.

Counterexample:
  1. 空 pattern → success=False
  2. path does not exist → success=False
  3. 路径是文件not目录 → success=False
  4. 无匹配 → success=True (not error)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from zall.tools.glob import GlobTool


@pytest.fixture
def glob_tree(tmp_path: Path) -> Path:
    """construct一个临时 glob 树."""
    (tmp_path / "a.py").write_text("", encoding="utf-8")
    (tmp_path / "b.py").write_text("", encoding="utf-8")
    (tmp_path / "c.txt").write_text("", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "d.py").write_text("", encoding="utf-8")
    (tmp_path / "sub" / "deep").mkdir()
    (tmp_path / "sub" / "deep" / "e.py").write_text("", encoding="utf-8")
    return tmp_path


class TestGlobInvariants:
    def test_glob_finds_py_files(self, glob_tree: Path) -> None:
        """Happy path: **/*.py 找到所有 .py file."""
        tool = GlobTool()
        result = tool.execute({"pattern": "**/*.py", "path": str(glob_tree)})
        assert result.success
        assert result.artifacts["match_count"] >= 4
        assert ".py" in result.output

    def test_empty_pattern_fails(self) -> None:
        """Counterexample: 空 pattern → success=False."""
        tool = GlobTool()
        result = tool.execute({"pattern": ""})
        assert result.success is False
        assert result.error is not None

    def test_nonexistent_path_fails(self, tmp_path: Path) -> None:
        """Counterexample: path does not exist → success=False."""
        tool = GlobTool()
        result = tool.execute({"pattern": "*", "path": str(tmp_path / "nope")})
        assert result.success is False

    def test_file_not_dir_fails(self, glob_tree: Path) -> None:
        """Counterexample: path 是filenotdirectory → success=False."""
        tool = GlobTool()
        result = tool.execute({"pattern": "*", "path": str(glob_tree / "a.py")})
        assert result.success is False

    def test_no_match_is_not_error(self, glob_tree: Path) -> None:
        """Counterexample: 无匹配 → success=True (not error)."""
        tool = GlobTool()
        result = tool.execute({"pattern": "*.nonexistent", "path": str(glob_tree)})
        assert result.success is True
        assert "no matches" in result.output.lower()
        assert result.artifacts["match_count"] == 0

    def test_tool_id_and_schema(self) -> None:
        """Happy path: tool_id non-空, schema valid."""
        tool = GlobTool()
        assert tool.tool_id == "glob"
        s = tool.schema
        assert s["type"] == "function"
        assert "pattern" in s["function"]["parameters"]["required"]
