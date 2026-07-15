"""list_dir tool invariant test (§4.2 tool layer).

IPR-0: each test must contain a counterexample.

Counterexample:
  1. 空 path → success=False
  2. path does not exist → success=False
  3. 路径是文件not目录 → success=False
  4. 噪声目录被跳过 (.git / __pycache__ / ...)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from zall.tools.list_dir import ListDirTool


@pytest.fixture
def dir_tree(tmp_path: Path) -> Path:
    """construct一个临时directory树."""
    (tmp_path / "file1.py").write_text("", encoding="utf-8")
    (tmp_path / "file2.txt").write_text("", encoding="utf-8")
    (tmp_path / "subdir").mkdir()
    (tmp_path / "subdir" / "nested.py").write_text("", encoding="utf-8")
    # 噪声directory (应被skip)
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "junk.pyc").write_text("", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("", encoding="utf-8")
    return tmp_path


class TestListDirInvariants:
    def test_list_dir_shows_tree(self, dir_tree: Path) -> None:
        """Happy path: 列出directory树, directory用 / 后缀."""
        tool = ListDirTool()
        result = tool.execute({"path": str(dir_tree)})
        assert result.success
        assert "subdir/" in result.output
        assert "file1.py" in result.output
        assert "file2.txt" in result.output

    def test_empty_path_fails(self) -> None:
        """Counterexample: 空 path → success=False."""
        tool = ListDirTool()
        result = tool.execute({"path": ""})
        assert result.success is False
        assert result.error is not None

    def test_nonexistent_path_fails(self, tmp_path: Path) -> None:
        """Counterexample: path does not exist → success=False."""
        tool = ListDirTool()
        result = tool.execute({"path": str(tmp_path / "nope")})
        assert result.success is False

    def test_file_not_dir_fails(self, dir_tree: Path) -> None:
        """Counterexample: path 是file → success=False."""
        tool = ListDirTool()
        result = tool.execute({"path": str(dir_tree / "file1.py")})
        assert result.success is False

    def test_noise_dirs_skipped(self, dir_tree: Path) -> None:
        """Counterexample: .git / __pycache__ must被skip (不出现在output).

        如果噪声目录不被跳过, 输出会被 .git 内部文件污染 context.
        """
        tool = ListDirTool()
        result = tool.execute({"path": str(dir_tree)})
        assert result.success
        assert "__pycache__" not in result.output
        assert ".git/" not in result.output
        assert "junk.pyc" not in result.output

    def test_depth_limit(self, dir_tree: Path) -> None:
        """Happy path: depth=1 时只列顶层, 不recursive."""
        tool = ListDirTool()
        result = tool.execute({"path": str(dir_tree), "depth": 1})
        assert result.success
        assert "subdir/" in result.output
        # nested.py 在 depth=2, 不应出现
        assert "nested.py" not in result.output

    def test_tool_id_and_schema(self) -> None:
        """Happy path: tool_id non-空, schema valid."""
        tool = ListDirTool()
        assert tool.tool_id == "list_dir"
        s = tool.schema
        assert s["type"] == "function"
        assert "path" in s["function"]["parameters"]["required"]
