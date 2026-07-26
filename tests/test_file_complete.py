"""Shared @-file completion (REPL + TUI parity) + workspace scan robustness.

Covers zall.cli.file_complete (single source used by both the inline REPL
completer and the full-screen TUI) and the REPL _DescCompleter @-path branch.

IPR-0: each test includes a counterexample.
"""

from __future__ import annotations

import os

import pytest

from zall.cli.file_complete import (
    clear_cache,
    expand_at_references,
    file_query,
    list_workspace_dirs,
    list_workspace_files,
    workspace_file_matches,
)


@pytest.fixture(autouse=True)
def _clear() -> None:
    clear_cache()
    yield
    clear_cache()


class TestFileQuery:
    def test_extraction_and_counterexamples(self) -> None:
        assert file_query("explain @src/lo") == "src/lo"
        assert file_query("@foo") == "foo"
        assert file_query("@") == ""
        # counterexamples (must be None)
        assert file_query("just text") is None
        assert file_query("mail a@b.com") is None      # @ 前非空白
        assert file_query("@a b") is None               # @token 后有空格
        assert file_query("") is None


def _mk_workspace(tmp_path) -> str:
    (tmp_path / "src" / "core").mkdir(parents=True)
    (tmp_path / "src" / "core" / "loop.py").write_text("x", encoding="utf-8")
    (tmp_path / "src" / "core" / "loop_config.py").write_text("x", encoding="utf-8")
    (tmp_path / "README.md").write_text("x", encoding="utf-8")
    (tmp_path / ".git").mkdir()  # noise dir → must be skipped
    (tmp_path / ".git" / "HEAD").write_text("x", encoding="utf-8")
    return str(tmp_path)


class TestWorkspaceScan:
    def test_matches_rank_and_skip_noise(self, tmp_path) -> None:
        root = _mk_workspace(tmp_path)
        files = list_workspace_files(root)
        assert "src/core/loop.py" in files
        assert "README.md" in files
        # counterexample: noise dir (.git) is NOT scanned
        assert not any(".git" in f for f in files)

    def test_basename_prefix_ranked_first(self, tmp_path) -> None:
        root = _mk_workspace(tmp_path)
        m = workspace_file_matches("loop", limit=8, root=root)
        # basename starting with 'loop' ranks before path-only matches
        assert m[0] == "src/core/loop.py"
        # counterexample: a non-matching query returns nothing
        assert workspace_file_matches("zzznope", root=root) == []

    def test_limit_respected(self, tmp_path) -> None:
        root = _mk_workspace(tmp_path)
        assert len(workspace_file_matches("", limit=2, root=root)) <= 2

    def test_cache_is_reused(self, tmp_path) -> None:
        root = _mk_workspace(tmp_path)
        first = list_workspace_files(root)
        # add a file AFTER first scan → cache should still return the old list
        (tmp_path / "late.py").write_text("x", encoding="utf-8")
        assert list_workspace_files(root) == first   # cached
        clear_cache()
        assert "late.py" in list_workspace_files(root)  # after clear, rescanned


class TestReplCompleter:
    def test_at_yields_files_slash_yields_commands(self, tmp_path) -> None:
        pytest.importorskip("prompt_toolkit")
        root = _mk_workspace(tmp_path)
        # point the scanner at our temp workspace
        import zall.cli.file_complete as fc
        fc._CACHE[os.getcwd()] = fc.list_workspace_files(root)

        from zall.cli.prompt import _build_custom_completer
        comp = _build_custom_completer(["/help", "/model"], None,
                                       {"/help": "help", "/model": "model"})

        class _Doc:
            def __init__(self, t: str) -> None:
                self.text_before_cursor = t

        at = [c.text for c in comp.get_completions(_Doc("open @loop"), None)]
        assert any(c.endswith("loop.py") for c in at)     # @ → files
        sl = [c.text for c in comp.get_completions(_Doc("/mod"), None)]
        assert "/model" in sl                              # / → commands
        # counterexample: plain text yields no file completions
        plain = list(comp.get_completions(_Doc("hello world"), None))
        assert all(not c.text.endswith(".py") for c in plain)


class TestExpandAtReferences:
    def test_real_file_injected(self, tmp_path) -> None:
        (tmp_path / "a.py").write_text("print(1)\nprint(2)\n", encoding="utf-8")
        out, injected = expand_at_references("explain @a.py please", root=str(tmp_path))
        assert injected == ["a.py"]
        assert '<file path="a.py">' in out
        assert "print(1)" in out
        assert out.startswith("explain @a.py please")  # 原文在前, 内容附后

    def test_non_file_ref_untouched(self, tmp_path) -> None:
        # 反例: @token 不是真实文件 → 原文不变, 不注入
        out, injected = expand_at_references("ping @nobody", root=str(tmp_path))
        assert injected == []
        assert out == "ping @nobody"

    def test_no_at_fast_path(self, tmp_path) -> None:
        out, injected = expand_at_references("hello world", root=str(tmp_path))
        assert injected == [] and out == "hello world"

    def test_binary_file_skipped_not_crash(self, tmp_path) -> None:
        (tmp_path / "b.bin").write_bytes(b"\x00\x01\x02ELF")
        out, injected = expand_at_references("look @b.bin", root=str(tmp_path))
        assert injected == ["b.bin"]
        assert "[binary file skipped]" in out  # 标注跳过, 不崩

    def test_per_file_truncation(self, tmp_path) -> None:
        (tmp_path / "big.txt").write_text("A" * 5000, encoding="utf-8")
        out, injected = expand_at_references("@big.txt", root=str(tmp_path), max_file_bytes=1000)
        assert injected == ["big.txt"]
        assert "truncated" in out
        # counterexample: full 5000 chars must NOT all be present
        assert out.count("A") <= 1200

    def test_dedupe_repeated_ref(self, tmp_path) -> None:
        (tmp_path / "a.py").write_text("x", encoding="utf-8")
        out, injected = expand_at_references("@a.py and again @a.py", root=str(tmp_path))
        assert injected == ["a.py"]  # 去重: 只注入一次


class TestDirSupport:
    def test_list_dirs_skips_noise(self, tmp_path) -> None:
        root = _mk_workspace(tmp_path)
        dirs = list_workspace_dirs(root)
        assert "src" in dirs and "src/core" in dirs
        assert not any(".git" in d for d in dirs)  # 反例: noise 目录不收录

    def test_matches_include_dirs(self, tmp_path) -> None:
        root = _mk_workspace(tmp_path)
        m = workspace_file_matches("core", root=root)
        assert "src/core/" in m            # 目录带末尾 / 参与补全
        # counterexample: include_dirs=False 时无目录
        m2 = workspace_file_matches("core", root=root, include_dirs=False)
        assert "src/core/" not in m2

    def test_expand_dir_injects_listing(self, tmp_path) -> None:
        root = _mk_workspace(tmp_path)
        # @src/ → 注入一层清单 (含子目录 core/), 非文件内容
        out, injected = expand_at_references("show @src/", root=root)
        assert injected == ["src/"]
        assert '<dir path="src/">' in out
        assert "core/" in out              # 子目录带 /
        # counterexample: 不应把目录当成文件读 (无 <file> 块)
        assert "<file" not in out

    def test_expand_dir_and_file_mixed(self, tmp_path) -> None:
        root = _mk_workspace(tmp_path)
        out, injected = expand_at_references("@README.md and @src", root=root)
        assert "README.md" in injected and "src/" in injected
        assert "<file" in out and "<dir" in out
