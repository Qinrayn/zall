"""write_file invariant test (ACI ACI design)."""

from __future__ import annotations

import os
import tempfile

import pytest

from zall.tools.write_file import WriteFileTool


@pytest.fixture
def tool() -> WriteFileTool:
    return WriteFileTool()


@pytest.fixture
def tmp_path() -> str:
    p = tempfile.mktemp(suffix=".txt")
    yield p
    try:
        os.unlink(p)
    except OSError:
        pass


class TestWriteFileHappyPath:
    def test_write_new_file(self, tool: WriteFileTool, tmp_path: str) -> None:
        result = tool.execute({"path": tmp_path, "content": "hello world"})
        assert result.success is True
        assert os.path.exists(tmp_path)
        with open(tmp_path, "r") as f:
            assert f.read() == "hello world"

    def test_create_only_fails_on_existing(self, tool: WriteFileTool, tmp_path: str) -> None:
        """Counterexample: create_only=True + file already exists → reject."""
        with open(tmp_path, "w") as f:
            f.write("old")
        result = tool.execute({"path": tmp_path, "content": "new", "create_only": True})
        assert result.success is False
        assert "already exists" in result.output.lower()

    def test_create_only_creates_new(self, tool: WriteFileTool, tmp_path: str) -> None:
        result = tool.execute({"path": tmp_path, "content": "new", "create_only": True})
        assert result.success is True

    def test_overwrite_existing(self, tool: WriteFileTool, tmp_path: str) -> None:
        with open(tmp_path, "w") as f:
            f.write("old")
        result = tool.execute({"path": tmp_path, "content": "new"})
        assert result.success is True
        with open(tmp_path, "r") as f:
            assert f.read() == "new"

    def test_auto_create_parent_dirs(self, tool: WriteFileTool) -> None:
        d = tempfile.mkdtemp()
        p = os.path.join(d, "sub", "file.txt")
        try:
            result = tool.execute({"path": p, "content": "nested"})
            assert result.success is True
            assert os.path.exists(p)
        finally:
            try:
                os.unlink(p)
                os.rmdir(os.path.join(d, "sub"))
                os.rmdir(d)
            except OSError:
                pass

    def test_artifacts(self, tool: WriteFileTool, tmp_path: str) -> None:
        result = tool.execute({"path": tmp_path, "content": "a\nb\nc"})
        assert result.success is True
        assert result.artifacts["lines"] == 3
        assert result.artifacts["created"] is True


class TestWriteFileCounterExamples:
    def test_empty_path(self, tool: WriteFileTool) -> None:
        result = tool.execute({"path": "", "content": "x"})
        assert result.success is False