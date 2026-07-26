"""工具集预设不变量 (PARADIGM Step 0 + 既有预设)。

不变量 (each with counterexample):
  I   已知预设返回其工具 ID 列表; 未知预设 → ValueError (反例)。
  II  归一化: 大小写/连字符不敏感。
  III lean 预设 = 少而宽 7 工具 (Bitter Lesson); 不含窄工具 (反例)。
  IV  build_native_tools_for_preset 产出真实工具实例, 每个有 tool_id。
"""

from __future__ import annotations

import pytest

from zall.core.toolset import (
    build_native_tools_for_preset,
    get_tool_ids_for_preset,
    list_presets,
)


class TestPresetIds:
    def test_known_presets(self) -> None:
        for p in ("zall", "explore", "plan", "codex", "opencode", "lean"):
            ids = get_tool_ids_for_preset(p)
            assert isinstance(ids, list) and ids                     # 非空

    def test_unknown_raises(self) -> None:
        with pytest.raises(ValueError):
            get_tool_ids_for_preset("nonesuch")                      # I 反例

    def test_normalization(self) -> None:
        assert get_tool_ids_for_preset("LEAN") == get_tool_ids_for_preset("lean")
        assert get_tool_ids_for_preset("Lean") == get_tool_ids_for_preset("lean")  # II

    def test_list_presets_includes_lean(self) -> None:
        presets = list_presets()
        assert "lean" in presets and "zall" in presets


class TestLeanPreset:
    def test_lean_is_few_and_broad(self) -> None:
        ids = get_tool_ids_for_preset("lean")
        assert set(ids) == {
            "bash", "read_file", "write_file", "edit_file", "grep", "glob", "list_dir",
        }
        # 反例: lean 不含窄/重工具 (Bitter Lesson: 少而宽)
        for narrow in ("web_fetch", "search", "spawn_subagent", "read_image", "batch_edit"):
            assert narrow not in ids

    def test_lean_smaller_than_zall(self) -> None:
        assert len(get_tool_ids_for_preset("lean")) < len(get_tool_ids_for_preset("zall"))


class TestBuildTools:
    def test_build_lean_instances(self) -> None:
        tools = build_native_tools_for_preset("lean")
        assert len(tools) == 7
        ids = {t.tool_id for t in tools}
        assert "bash" in ids and "read_file" in ids                  # IV


class TestLeanPrompt:
    def test_lean_prompt_skips_heavy_sections(self) -> None:
        from types import SimpleNamespace

        from zall.cli.environment import (
            CwdMeta,
            build_system_prompt,
            clear_system_prompt_cache,
        )
        from zall.core.context import Context

        clear_system_prompt_cache()
        ctx = Context(user_raw="do a task", cwd_meta=CwdMeta())
        skill = SimpleNamespace(name="mytoolz", description="does things")
        full = build_system_prompt(ctx, skills=[skill], lean=False)
        lean = build_system_prompt(ctx, skills=[skill], lean=True)
        # lean 跳过 skills 段 → 不含 skill 名; full 含
        assert "mytoolz" in full and "mytoolz" not in lean
        assert len(lean) < len(full)                    # 更短 (省 token)
        assert "autonomous coding agent" in lean        # 反例: base 仍保留
