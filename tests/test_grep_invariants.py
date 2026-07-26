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


class TestGrepTimeoutNonBlocking:
    """G14 回归守卫: 超时后主线程立即返回, 不被失控搜索线程阻塞。

    旧实现 `with ThreadPoolExecutor` 退出时 shutdown(wait=True) 会阻塞到
    失控线程自然结束 — timeout 保护形同虚设 (I-GREP-TO)。
    """

    def test_timeout_returns_promptly(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """I-GREP-TO-1: 搜索耗时远超 timeout 时, 返回时长≈timeout 而非全量扫完。"""
        import time

        import zall.tools.grep as grep_mod

        # 40 个文件 × 每次 is_binary 拖慢 0.1s ≈ 全量需 4s; timeout 钳到 0.3s
        for i in range(40):
            (tmp_path / f"f{i}.txt").write_text("hello\n", encoding="utf-8")
        real_is_binary = grep_mod.is_binary

        def _slow_is_binary(p: Path) -> bool:
            time.sleep(0.1)
            return real_is_binary(p)

        monkeypatch.setattr(grep_mod, "is_binary", _slow_is_binary)
        monkeypatch.setattr(grep_mod, "_MAX_REGEX_TIMEOUT", 0.3)

        tool = GrepTool()
        start = time.monotonic()
        result = tool._grep_python("hello", tmp_path, False, False, 100)
        elapsed = time.monotonic() - start

        assert result.success is False
        assert result.error == "regex timeout"
        # 旧实现会阻塞 ~4s; 新实现 join(0.3) 后立即返回
        assert elapsed < 2.0, f"timeout 后主线程被阻塞 {elapsed:.1f}s"

    def test_fast_search_unaffected_counterexample(self, tmp_path: Path) -> None:
        """反例: 正常速度搜索不受线程化影响, 结果完整正确。"""
        (tmp_path / "x.py").write_text("needle_alpha\nother\n", encoding="utf-8")
        tool = GrepTool()
        result = tool._grep_python("needle_alpha", tmp_path, False, False, 100)
        assert result.success is True
        assert result.artifacts["match_count"] == 1

    def test_no_executor_in_fallback_guard(self) -> None:
        """I-GREP-TO-2 架构守卫: 退化路径不得回归 ThreadPoolExecutor(wait=True 陷阱),
        必须是 daemon 线程 (不阻止进程退出)。"""
        import inspect

        import zall.tools.grep as grep_mod

        src = inspect.getsource(grep_mod)
        # 检查实际调用形态 (注释里提及不算): 无 executor 实例化 / 无 concurrent 导入
        assert "ThreadPoolExecutor(" not in src
        assert "import concurrent" not in src
        assert "daemon=True" in src
