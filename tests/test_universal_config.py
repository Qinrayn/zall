"""通用多来源配置 + 命令面板滚动 test.

covers:
  1. 命令面板 (CommandMenu): 保留全部命令 + 滚动窗口 + 位置计数 (修 “/ 显示不完全”)
  2. 通用接入: [model] 一处配齐 name+api_base+api_key+provider → load_config 解析
  3. _detect_provider 尊重显式 [model].provider (绕过前缀推断, 任意来源可接入)
  4. /config guide 讲清 3 字段通用接入; provider 可经 /config set 设置

IPR-0: 每个 test 含 counterexample。
"""

from __future__ import annotations

import io

from rich.console import Console


def _plain(renderable) -> str:
    buf = io.StringIO()
    Console(file=buf, width=88, no_color=True).print(renderable)
    return buf.getvalue()


# ──────────────────────────────────────────────────────────────────────────
# 1. 命令面板滚动 (修 “/ 命令显示不完全”)
# ──────────────────────────────────────────────────────────────────────────


class TestCommandPaletteScroll:
    def test_keeps_all_items_not_capped(self) -> None:
        """Happy path: 全部命令保留 (不再 [:8] 截断丢失)。"""
        from zall.cli.tui.widgets import CommandMenu
        m = CommandMenu()
        m.update_items([(f"cmd{i}", f"d{i}") for i in range(30)], prefix="/")
        assert len(m._items) == 30

    def test_scroll_window_reaches_all(self) -> None:
        """Happy path: 向上环绕到末项时窗口滚到底 (可达全部)。"""
        from zall.cli.tui.widgets import CommandMenu
        m = CommandMenu()
        m.update_items([(f"cmd{i}", "") for i in range(30)], prefix="/")
        m.move_selection(-1)          # wrap → 末项
        assert m._selected == 29
        assert m._offset > 0          # 窗口已滚动 (末项可见)
        assert m.selected_command == "cmd29"

    def test_position_counter_shown(self) -> None:
        """Counterexample: 超窗口时渲染带位置计数 (告知还有更多, 非只 8 条)。"""
        from zall.cli.tui.widgets import CommandMenu
        m = CommandMenu()
        m.update_items([(f"cmd{i}", "") for i in range(30)], prefix="/", hint="h")
        assert "1/30" in _plain(m.render())

    def test_small_list_no_counter(self) -> None:
        """Counterexample: 项数不超窗口 → 不显位置计数 (无多余噪音)。"""
        from zall.cli.tui.widgets import CommandMenu
        m = CommandMenu()
        m.update_items([("a", ""), ("b", "")], prefix="/", hint="h")
        out = _plain(m.render())
        assert "/2" not in out


# ──────────────────────────────────────────────────────────────────────────
# 2. 通用接入: [model] 一处配齐
# ──────────────────────────────────────────────────────────────────────────


def _write_cfg(cfgdir, **model_fields) -> None:
    cfgdir.mkdir(parents=True, exist_ok=True)
    lines = ["[model]"]
    for k, v in model_fields.items():
        lines.append(f'{k} = "{v}"')
    (cfgdir / "config.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")


class TestUniversalModelConfig:
    def test_one_block_all_fields(self, tmp_path, monkeypatch) -> None:
        """Happy path: [model] 内 name+api_base+api_key+provider 全解析 (apikey+baseurl+id 一处配齐)。"""
        cfgdir = tmp_path / ".zall"
        _write_cfg(
            cfgdir, name="any-model", api_base="https://x.example.com/v1",
            api_key="sk-inline", provider="openai",
        )
        monkeypatch.setattr("zall.safety.config.CONFIG_DIR", cfgdir)
        work = tmp_path / "work"; work.mkdir()
        monkeypatch.chdir(work)
        from zall.safety.config import load_config
        cfg = load_config()
        assert cfg["model"] == "any-model"
        assert cfg["api_base"] == "https://x.example.com/v1"
        assert cfg["api_key"] == "sk-inline"
        assert cfg["provider"] == "openai"

    def test_no_inline_key_keeps_default_empty(self, tmp_path, monkeypatch) -> None:
        """Counterexample: 不写 [model].api_key → 不误填 (provider 也空)。"""
        cfgdir = tmp_path / ".zall"
        _write_cfg(cfgdir, name="m", api_base="https://y/v1")
        monkeypatch.setattr("zall.safety.config.CONFIG_DIR", cfgdir)
        work = tmp_path / "work"; work.mkdir()
        monkeypatch.chdir(work)
        from zall.safety.config import load_config
        cfg = load_config()
        assert cfg["provider"] == ""          # 未设 → 空 (回落前缀推断)
        assert cfg["api_key"] == ""            # 未内联 → 空 (走 [auth]/env)


# ──────────────────────────────────────────────────────────────────────────
# 3. _detect_provider 尊重显式 provider
# ──────────────────────────────────────────────────────────────────────────


class TestExplicitProviderRouting:
    def test_config_provider_overrides_prefix(self, monkeypatch) -> None:
        """Happy path: [model].provider 显式指定 → 绕过模型名前缀推断。"""
        from zall.cli import config as cc
        monkeypatch.delenv("ZALL_PROVIDER", raising=False)
        monkeypatch.setattr(
            "zall.safety.config.load_config",
            lambda: {"provider": "anthropic", "model": "gpt-4o"},
        )
        # 模型名 gpt-4o 本会前缀匹配 openai; 显式 provider=anthropic 应胜出
        assert cc._detect_provider("gpt-4o") == "anthropic"

    def test_no_provider_falls_back_to_prefix(self, monkeypatch) -> None:
        """Counterexample: 未设 provider → 回落模型名前缀推断 (gpt- → openai)。"""
        from zall.cli import config as cc
        monkeypatch.delenv("ZALL_PROVIDER", raising=False)
        monkeypatch.setattr(
            "zall.safety.config.load_config",
            lambda: {"provider": "", "model": "gpt-4o"},
        )
        assert cc._detect_provider("gpt-4o") == "openai"


# ──────────────────────────────────────────────────────────────────────────
# 4. /config guide + provider 可设
# ──────────────────────────────────────────────────────────────────────────


class TestConfigGuide:
    def test_guide_shows_three_field_setup(self) -> None:
        """Happy path: /config guide 讲清 api_key + base_url + model 的通用接入 + [model] 块。"""
        from zall.cli.commands.config import _show_guide
        out = io.StringIO()
        _show_guide(out)
        s = out.getvalue()
        assert "api_base" in s and "api_key" in s
        assert "[model]" in s
        assert "provider" in s
        assert "3 fields" in s.lower() or "any model source" in s.lower()

    def test_provider_is_settable_key(self) -> None:
        """Counterexample: provider 现为 /config set 合法 key (env=ZALL_PROVIDER)。"""
        from zall.cli.commands.config import _CONFIG_KEYS
        assert "provider" in _CONFIG_KEYS
        assert _CONFIG_KEYS["provider"][2] == "ZALL_PROVIDER"
