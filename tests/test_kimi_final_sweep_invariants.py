"""kimi 残值终扫吸纳项不变量测试 (删除 kimi-cli-main 前的最后一批).

IPR-0: each test must contain a counterexample.

Protected invariants:
  I-TAIL-1:  read_file 负 offset 尾读 — 返回最后 |offset| 行 + 精确总行数;
             正 offset 行为不变 (反例孪生)。
  I-SKILLDIR-1: 目录式 SKILL.md 发现 (多品牌复用) — frontmatter 解析 +
             渐进披露 prompt; toml 同名覆盖目录技能 (优先级反例)。
  I-SUMMARY-ERR: 规则折叠摘要把工具错误置于最高优先段 (反例: 无错误无该段)。
  I-BASH-STDIN: bash 子进程 stdin 接 DEVNULL (交互式提示得 EOF 不挂死)。
"""

from __future__ import annotations

from pathlib import Path

import pytest

# ── I-TAIL-1: read_file 负 offset 尾读 ──


def test_read_file_negative_offset_reads_tail(tmp_path: Path) -> None:
    from zall.tools.read_file import ReadFileTool
    f = tmp_path / "log.txt"
    f.write_text("\n".join(f"line{i}" for i in range(1, 51)), encoding="utf-8")
    result = ReadFileTool().execute({"path": str(f), "offset": -5})
    assert result.success
    assert "line50" in result.output and "line46" in result.output
    assert "line45" not in result.output          # 反例: 尾窗之外不出现
    assert result.artifacts["total_lines"] == 50  # 精确总行数
    assert result.artifacts["lines_read"] == 5


def test_read_file_positive_offset_unchanged(tmp_path: Path) -> None:
    """反例孪生: 正 offset 语义不受尾读改动影响。"""
    from zall.tools.read_file import ReadFileTool
    f = tmp_path / "a.txt"
    f.write_text("\n".join(f"L{i}" for i in range(1, 21)), encoding="utf-8")
    result = ReadFileTool().execute({"path": str(f), "offset": 3, "limit": 2})
    assert result.success
    assert "L3" in result.output and "L4" in result.output
    assert "L5" not in result.output


def test_read_file_tail_of_empty_file(tmp_path: Path) -> None:
    from zall.tools.read_file import ReadFileTool
    f = tmp_path / "empty.txt"
    f.write_text("", encoding="utf-8")
    result = ReadFileTool().execute({"path": str(f), "offset": -10})
    assert result.success
    assert "empty" in result.output


# ── I-SKILLDIR-1: 多品牌目录式技能发现 ──


def test_skill_md_discovery_and_progressive_disclosure(tmp_path: Path) -> None:
    from zall.skills.loader import load_skill_dirs
    d = tmp_path / "claude_skills"
    (d / "pdf-tools").mkdir(parents=True)
    (d / "pdf-tools" / "SKILL.md").write_text(
        "---\nname: pdf\ndescription: Process PDF files\n---\n"
        "# Very long instructions...\n" + "body\n" * 500,
        encoding="utf-8",
    )
    skills = load_skill_dirs([d])
    assert len(skills) == 1
    s = skills[0]
    assert s.name == "pdf"                        # frontmatter name 优先
    assert s.description == "Process PDF files"
    # 渐进披露: prompt 指向文件而非内嵌 500 行正文
    assert "SKILL.md" in s.prompt and "body" not in s.prompt
    assert "{input}" in s.prompt                  # 参数占位保留


def test_skill_md_defaults_to_dir_name_without_frontmatter(tmp_path: Path) -> None:
    from zall.skills.loader import load_skill_dirs
    d = tmp_path / "skills"
    (d / "my-skill").mkdir(parents=True)
    (d / "my-skill" / "SKILL.md").write_text("no frontmatter here", encoding="utf-8")
    skills = load_skill_dirs([d])
    assert skills and skills[0].name == "my-skill"


def test_toml_skill_overrides_dir_skill(tmp_path: Path) -> None:
    """优先级反例: 同名时 toml (自家) 覆盖品牌目录技能。"""
    from zall.skills.loader import load_skills
    d = tmp_path / "brand"
    (d / "review").mkdir(parents=True)
    (d / "review" / "SKILL.md").write_text(
        "---\nname: review\n---\nbrand version", encoding="utf-8")
    user_toml = tmp_path / "skills.toml"
    user_toml.write_text(
        '[[skills]]\nname = "review"\ndescription = "mine"\n'
        'prompt = "zall native version"\n', encoding="utf-8")
    skills = load_skills(user_path=str(user_toml),
                         project_path=str(tmp_path / "noproj"),
                         skill_dirs=[d])
    by_name = {s.name: s for s in skills}
    assert by_name["review"].prompt == "zall native version"


def test_missing_dirs_are_silent(tmp_path: Path) -> None:
    """反例: 不存在的目录静默跳过 (IPR-0)。"""
    from zall.skills.loader import load_skill_dirs
    assert load_skill_dirs([tmp_path / "nope", tmp_path / "also_nope"]) == []


# ── I-SUMMARY-ERR: 压缩摘要错误最高优先 ──


def test_summary_puts_errors_first() -> None:
    from zall.core.compactor import ModelCompactor
    from zall.core.model import Message
    msgs = [
        Message.user("do the thing"),
        Message.tool_result(content="[ERROR: file not found: x.py]",
                            tool_call_id="c1", tool_id="read_file"),
        Message.assistant(content="hmm"),
    ]
    summary = ModelCompactor()._generate_summary(msgs, model=None)
    assert "Errors seen:" in summary
    assert "file not found" in summary
    assert summary.index("Errors seen:") < summary.index("Messages:")


def test_summary_no_error_section_when_clean() -> None:
    """反例孪生: 无错误 → 无 Errors 段。"""
    from zall.core.compactor import ModelCompactor
    from zall.core.model import Message
    msgs = [Message.user("hi"), Message.assistant(content="hello")]
    summary = ModelCompactor()._generate_summary(msgs, model=None)
    assert "Errors seen:" not in summary


# ── I-BASH-STDIN: 交互式提示不挂死 ──


def test_bash_interactive_prompt_gets_eof_not_hang() -> None:
    """反例基准: 读 stdin 的命令必须立刻拿到 EOF 结束, 而非挂到 timeout。

    Windows/Unix 通吃: python -c 读 stdin — stdin=DEVNULL 时 read() 立即返回空。
    """
    import time

    from zall.tools.bash import BashTool
    t0 = time.monotonic()
    result = BashTool().execute({
        "command": 'python -c "import sys; d=sys.stdin.read(); print(\'got:\'+repr(d))"',
        "timeout": 20,
    })
    elapsed = time.monotonic() - t0
    assert elapsed < 15, f"stdin read must get EOF fast, took {elapsed:.1f}s"
    assert result.success
    assert "got:''" in result.output.replace('"', "'")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
