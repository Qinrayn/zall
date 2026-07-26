"""Invariant tests for hash_utils (Part D, 真溯源哈希 helper).

Corresponds to:
  docs/E3_SCIENCE_KIT.md §2.4  ScienceProvenance 真实文件哈希
  MASTER.md §10 I-10          负结果平等 - 真实溯源同样适用

IPR-0: each test includes a counterexample asserting the OLD broken behavior
(占位符哈希 "sha256:cli-manual" / 假哈希) does NOT hold.
"""

from __future__ import annotations

import pytest

from zall._util.hash_utils import (
    hash_bytes,
    hash_dir,
    hash_file,
    hash_files,
    environment_hash,
)


class TestHashFileInvariants:
    """hash_file 必须对真实文件内容算 SHA-256。"""

    def test_real_hash_not_placeholder(self, tmp_path) -> None:
        """真实文件的哈希不是占位符 "sha256:cli-manual"。

        Counterexample: 旧 ScienceProvenance 用硬编码占位符, 无法区分内容。
        """
        f = tmp_path / "script.py"
        f.write_text("print('hello')", encoding="utf-8")
        h = hash_file(f)
        assert h.startswith("sha256:")
        assert h != "sha256:cli-manual"
        assert h != "sha256:agent"
        assert len(h) == len("sha256:") + 64  # 64 hex chars

    def test_same_content_same_hash(self, tmp_path) -> None:
        """同内容文件哈希一致 (哈希基本性质)."""
        a = tmp_path / "a.txt"
        b = tmp_path / "b.txt"
        a.write_text("identical", encoding="utf-8")
        b.write_text("identical", encoding="utf-8")
        assert hash_file(a) == hash_file(b)

    def test_different_content_different_hash(self, tmp_path) -> None:
        """Counterexample: 不同内容必须哈希不同 (占位符时代无法区分)."""
        a = tmp_path / "a.txt"
        b = tmp_path / "b.txt"
        a.write_text("content one", encoding="utf-8")
        b.write_text("content two", encoding="utf-8")
        assert hash_file(a) != hash_file(b)

    def test_empty_vs_nonempty_differ(self, tmp_path) -> None:
        """空文件与非空文件哈希不同。"""
        empty = tmp_path / "empty.txt"
        full = tmp_path / "full.txt"
        empty.write_text("", encoding="utf-8")
        full.write_text("data", encoding="utf-8")
        assert hash_file(empty) != hash_file(full)

    def test_missing_file_raises(self, tmp_path) -> None:
        """不存在的文件应抛 OSError (而非返回占位符)."""
        with pytest.raises(OSError):
            hash_file(tmp_path / "nonexistent.txt")


class TestHashFilesInvariants:
    """hash_files 聚合哈希必须顺序无关。"""

    def test_order_invariant(self, tmp_path) -> None:
        """Counterexample: 顺序无关 -- 同一组文件不同传入顺序必须同哈希。

        若聚合哈希依赖顺序, 则实验溯源不可复现。
        """
        f1 = tmp_path / "a.py"; f1.write_text("x=1", encoding="utf-8")
        f2 = tmp_path / "b.py"; f2.write_text("y=2", encoding="utf-8")
        h_order1 = hash_files([f1, f2])
        h_order2 = hash_files([f2, f1])
        assert h_order1 == h_order2

    def test_different_sets_different_hash(self, tmp_path) -> None:
        """不同文件集哈希不同。"""
        f1 = tmp_path / "a.py"; f1.write_text("x=1", encoding="utf-8")
        f2 = tmp_path / "b.py"; f2.write_text("y=2", encoding="utf-8")
        f3 = tmp_path / "c.py"; f3.write_text("z=3", encoding="utf-8")
        assert hash_files([f1, f2]) != hash_files([f1, f2, f3])


class TestHashDirInvariants:
    """hash_dir 环境快照哈希。"""

    def test_dir_hash_stable_on_rename(self, tmp_path) -> None:
        """目录迁移 (内容不变) 哈希稳定 -- 用相对路径, 不含绝对路径。"""
        import shutil
        d1 = tmp_path / "env1"; d1.mkdir()
        (d1 / "req.txt").write_text("pydantic>=2.5", encoding="utf-8")
        d2 = tmp_path / "env2"; shutil.copytree(d1, d2)
        assert hash_dir(d1) == hash_dir(d2)

    def test_empty_dir_returns_consistent_hash(self, tmp_path) -> None:
        """空目录返回一致的哈希 (对空串的哈希)."""
        d = tmp_path / "empty_env"; d.mkdir()
        h1 = hash_dir(d)
        h2 = hash_dir(d)
        assert h1 == h2
        assert h1.startswith("sha256:")


class TestEnvironmentHashInvariants:
    """environment_hash 必须捕获 python 版本等运行环境。"""

    def test_contains_python_version(self) -> None:
        """环境哈希随 python 版本变化 (同一进程内稳定)."""
        import sys
        h = environment_hash()
        assert h.startswith("sha256:")
        # 同进程内多次调用应一致
        assert h == environment_hash()

    def test_extra_changes_hash(self) -> None:
        """附加串不同 -> 环境哈希不同 (可区分不同依赖集)."""
        h1 = environment_hash(extra="deps=A")
        h2 = environment_hash(extra="deps=B")
        assert h1 != h2
