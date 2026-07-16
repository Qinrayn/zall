"""zall.tools.batch_edit — invariant tests.

IPR-0 Counterexample:
  - 空 edits → fail
  - file not found → fail, 不修改任何文件
  - old_string no match → fail, 报告具体位置
  - old_string 匹配多次 → fail, 列出所有匹配位置
  - 多文件, 部分fail → 全部不修改
  - 多文件, 全部成功 → returns每个文件的 diff 摘要
  - 超过 max edits → 拒绝
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

from zall.tools.batch_edit import BatchEditTool


def _make_files(tmp: str, files: dict[str, str]) -> dict[str, Path]:
    """in temp directory中createtestfile, returns {name: Path}."""
    paths = {}
    for name, content in files.items():
        p = Path(tmp) / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        paths[name] = p
    return paths


def test_empty_edits() -> None:
    """空 edits → fail."""
    tool = BatchEditTool()
    result = tool.execute({"edits": []})
    assert not result.success
    assert "edits" in result.error


def test_missing_edits() -> None:
    """无 edits parameter → fail."""
    tool = BatchEditTool()
    result = tool.execute({})
    assert not result.success


def test_file_not_found() -> None:
    """file not found → fail, 不修改任何file."""
    tool = BatchEditTool()
    result = tool.execute({
        "edits": [{"path": "/nonexistent/file.py", "old_string": "x", "new_string": "y"}]
    })
    assert not result.success
    assert "not found" in result.output


def test_old_string_not_found() -> None:
    """old_string no match → fail, 报告concreteinformation."""
    with tempfile.TemporaryDirectory() as tmp:
        _make_files(tmp, {"test.py": "hello world"})
        tool = BatchEditTool()
        result = tool.execute({
            "edits": [{"path": os.path.join(tmp, "test.py"), "old_string": "zzz", "new_string": "yyy"}]
        })
        assert not result.success
        assert "not found" in result.output


def test_old_string_multiple_matches() -> None:
    """old_string 匹配多次 → fail, 列出所有匹配位置."""
    with tempfile.TemporaryDirectory() as tmp:
        _make_files(tmp, {"test.py": "foo\nbar\nfoo\nbaz"})
        tool = BatchEditTool()
        result = tool.execute({
            "edits": [{"path": os.path.join(tmp, "test.py"), "old_string": "foo", "new_string": "qux"}]
        })
        assert not result.success
        assert "matched 2 times" in result.output


def test_single_file_success() -> None:
    """单fileedit成功."""
    with tempfile.TemporaryDirectory() as tmp:
        p = _make_files(tmp, {"test.py": "hello world"})["test.py"]
        tool = BatchEditTool()
        result = tool.execute({
            "edits": [{"path": str(p), "old_string": "hello", "new_string": "hi"}]
        })
        assert result.success
        assert p.read_text(encoding="utf-8") == "hi world"
        assert "1 file(s) edited" in result.output
        assert result.artifacts["ok"] == 1
        assert result.artifacts["failed"] == 0


def test_multi_file_success() -> None:
    """多file全部edit成功."""
    with tempfile.TemporaryDirectory() as tmp:
        paths = _make_files(tmp, {
            "a.py": "hello alpha",
            "b.py": "hello beta",
        })
        tool = BatchEditTool()
        result = tool.execute({
            "edits": [
                {"path": str(paths["a.py"]), "old_string": "hello", "new_string": "hi"},
                {"path": str(paths["b.py"]), "old_string": "hello", "new_string": "hey"},
            ]
        })
        assert result.success
        assert paths["a.py"].read_text(encoding="utf-8") == "hi alpha"
        assert paths["b.py"].read_text(encoding="utf-8") == "hey beta"
        assert result.artifacts["ok"] == 2


def test_partial_failure_rollback() -> None:
    """部分fail → 全部不修改 (原sub性)."""
    with tempfile.TemporaryDirectory() as tmp:
        paths = _make_files(tmp, {
            "a.py": "hello alpha",
            "b.py": "hello beta",
        })
        a_content_before = paths["a.py"].read_text(encoding="utf-8")
        tool = BatchEditTool()
        # b.py 的 old_string no match
        result = tool.execute({
            "edits": [
                {"path": str(paths["a.py"]), "old_string": "hello", "new_string": "hi"},
                {"path": str(paths["b.py"]), "old_string": "zzzzz", "new_string": "yyy"},
            ]
        })
        assert not result.success
        # a.py 不得被修改 (原sub性)
        assert paths["a.py"].read_text(encoding="utf-8") == a_content_before
        assert "validation failed" in result.error


def test_too_many_edits() -> None:
    """超过最大edit数 → reject."""
    from zall.tools.batch_edit import _MAX_EDITS
    with tempfile.TemporaryDirectory() as tmp:
        _make_files(tmp, {"a.py": "x"})
        tool = BatchEditTool()
        many_edits = [
            {"path": os.path.join(tmp, "a.py"), "old_string": "x", "new_string": "y"}
            for _ in range(_MAX_EDITS + 1)
        ]
        result = tool.execute({"edits": many_edits})
        assert not result.success
        assert "too many edits" in result.error


def test_diff_in_artifacts() -> None:
    """成功edit后, artifacts 中包含 diff."""
    with tempfile.TemporaryDirectory() as tmp:
        p = _make_files(tmp, {"test.py": "hello\nworld"})["test.py"]
        tool = BatchEditTool()
        result = tool.execute({
            "edits": [{"path": str(p), "old_string": "hello", "new_string": "hi"}]
        })
        assert result.success
        diffs = result.artifacts.get("diffs", {})
        # Normalize paths: macOS /var -> /private/var symlink
        p_resolved = str(Path(p).resolve())
        assert any(
            str(Path(k).resolve()) == p_resolved for k in diffs
        ), f"Path {p} not found in diffs keys: {list(diffs.keys())}"
        matched_key = next(k for k in diffs if str(Path(k).resolve()) == p_resolved)
        assert "hello" in diffs[matched_key] or "hi" in diffs[matched_key]


def test_integration_preserves_unchanged_files() -> None:
    """未涉及的file不改动."""
    with tempfile.TemporaryDirectory() as tmp:
        paths = _make_files(tmp, {
            "a.py": "hello",
            "b.py": "world",
        })
        b_before = paths["b.py"].read_text(encoding="utf-8")
        tool = BatchEditTool()
        result = tool.execute({
            "edits": [{"path": str(paths["a.py"]), "old_string": "hello", "new_string": "hi"}]
        })
        assert result.success
        assert paths["b.py"].read_text(encoding="utf-8") == b_before
        assert paths["a.py"].read_text(encoding="utf-8") == "hi"