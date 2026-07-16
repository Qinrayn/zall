"""project memory (§9.4 AGENTS.md) injection invariant tests.

IPR-0: includes counterexamples; construct violations that should cause tests to fail.

covers:
  §9.4  REPL/run 启动读 .zall/AGENTS.md injection system prompt
  IPR-0 Counterexample: 文件缺失 / 读取异常 → 静默does not crash, 不injection
"""

from __future__ import annotations

from pathlib import Path

import pytest

from zall.cli.environment import CwdMeta as _CwdMeta, read_agents_md as _read_agents_md, build_system_prompt as _build_system_prompt
from zall.core.context import Context


def _ctx(cwd: str) -> Context:
    return Context(user_raw="x", cwd_meta=_CwdMeta())


def test_read_agents_md_present(tmp_path: Path) -> None:
    """§9.4: .zall/AGENTS.md 存在 → 读到content."""
    (tmp_path / ".zall").mkdir()
    (tmp_path / ".zall" / "AGENTS.md").write_text(
        "# 项目约定\n- 用 pytest", encoding="utf-8"
    )
    got = _read_agents_md(str(tmp_path))
    assert got is not None
    assert "pytest" in got


def test_read_agents_md_absent_returns_none(tmp_path: Path) -> None:
    """§9.4: file缺失 → returns None (不强制 init, 不阻断)."""
    got = _read_agents_md(str(tmp_path))
    assert got is None


def test_read_agents_md_read_error_silent(tmp_path: Path, monkeypatch) -> None:
    """IPR-0 Counterexample: read抛exception (authority/encoding) → 静默returns None, 不得上抛."""
    def _boom(*a, **k):
        raise OSError("permission denied")

    monkeypatch.setattr(Path, "read_text", _boom)
    got = _read_agents_md(str(tmp_path))
    assert got is None


def test_build_system_prompt_injects_memory(tmp_path: Path) -> None:
    """§9.4: system prompt 含 PROJECT MEMORY 段 (来自 AGENTS.md).

    _CwdMeta.__init__ 把 cwd_path 设for实例属性, 故directly赋值实例属性
    (不依赖 monkeypatch 类属性, 那会被实例属性遮蔽).
    """
    (tmp_path / ".zall").mkdir()
    (tmp_path / ".zall" / "AGENTS.md").write_text(
        "## 约定\n- 禁止directly push main", encoding="utf-8"
    )
    ctx = Context(user_raw="x", cwd_meta=_CwdMeta())
    ctx.cwd_meta.cwd_path = str(tmp_path)  # 实例属性, directlycovers
    prompt = _build_system_prompt(ctx)
    assert "PROJECT MEMORY" in prompt
    assert "禁止directly push main" in prompt


def test_build_system_prompt_no_memory_when_absent(tmp_path: Path) -> None:
    """§9.4: 无 AGENTS.md → system prompt 不含 PROJECT MEMORY 段 (仍正常)."""
    ctx = Context(user_raw="x", cwd_meta=_CwdMeta())
    ctx.cwd_meta.cwd_path = str(tmp_path)
    prompt = _build_system_prompt(ctx)
    assert "PROJECT MEMORY" not in prompt
    assert "ENVIRONMENT" in prompt  # basic段仍在
