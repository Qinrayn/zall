"""Config corruption self-heal — duplicate [auth]/[model] sections.

Regression for the real bug found via dogfooding: repeated /model -p + onboarding
appended duplicate [auth]/[model] sections, growing the file unbounded. The TOML
loader only survived by luck (strict parsers reject duplicate tables). This locks in:

  1. Loader robustness: duplicate sections parse (last-value-wins), never crash.
  2. save_api_key dedup: rewrites to exactly one [auth] + one [model] (self-heal).
  3. _persist_model_to_config dedup: same.

IPR-0: each test includes a counterexample (asserts the corruption is NOT present).
"""

from __future__ import annotations

import pytest

_CORRUPT = """# zall config
[auth]
[auth]
    api_key = "sk-FIRST"
[auth]
api_key = "sk-LAST"

[model]
[model]
    name = "agnes-2.5-flash"
[model]
name = "agnes-2.0-flash"
api_base = "https://apihub.agnes-ai.com/v1"
"""


def _count(text: str, header: str) -> int:
    return sum(1 for ln in text.splitlines() if ln.strip() == header)


class TestLoaderRobustness:
    def test_duplicate_sections_last_wins(self, tmp_path) -> None:
        from zall._util.toml import load_toml_simple
        p = tmp_path / "config.toml"
        p.write_text(_CORRUPT, encoding="utf-8")
        data = load_toml_simple(p)  # must NOT raise
        # last-value-wins across duplicate tables
        assert data["auth"]["api_key"] == "sk-LAST"
        assert data["model"]["name"] == "agnes-2.0-flash"
        # counterexample: the earlier values must NOT survive
        assert data["auth"]["api_key"] != "sk-FIRST"
        assert data["model"]["name"] != "agnes-2.5-flash"

    def test_utf8_bom_still_parses_strict(self, tmp_path) -> None:
        """2026-09-18 实测: 编辑器留下的 UTF-8 BOM 让 tomllib 拒收, 静默回落
        宽松解析器后 [[providers]] 的数组/数字全部失效 (model_prefixes 变成
        单字符元组, window_size 失效 → 状态栏显示 32k)。修复: 读取时剥 BOM。"""
        import sys

        from zall._util.toml import load_toml_simple, _load_toml_fallback
        body = ('# zall config\n'
                '[model]\nname = "m"\nwindow_size = 128000\nprice_in = 0.14\n'
                'flag = true\n'
                '[[providers]]\nname = "gw"\n'
                'model_prefixes = ["deepseek-v4-flash", "gw-"]\n')
        p = tmp_path / "config.toml"
        p.write_bytes(b"\xef\xbb\xbf" + body.encode("utf-8"))

        data = load_toml_simple(p)
        assert data["model"]["window_size"] == 128000
        assert isinstance(data["model"]["window_size"], int)
        assert data["model"]["price_in"] == 0.14
        assert data["model"]["flag"] is True
        assert data["providers"][0]["model_prefixes"] == ["deepseek-v4-flash", "gw-"]
        # counterexample: 不再退化为字符串/单字符序列
        assert data["providers"][0]["model_prefixes"] != '["deepseek-v4-flash", "gw-"]'

        # 宽松解析器自身也要给出一致结果 (3.10 无 tomli 时是唯一路径)
        if sys.version_info >= (3, 11):
            fb = _load_toml_fallback(p)
            assert fb["providers"][0]["model_prefixes"] == ["deepseek-v4-flash", "gw-"]
            assert fb["model"]["window_size"] == 128000

    def test_quoted_bracket_string_is_not_an_array(self, tmp_path) -> None:
        """Counterexample: 带引号的 "[...]" 是字符串, 不被当数组拆开。"""
        from zall._util.toml import _load_toml_fallback
        p = tmp_path / "cfg.toml"
        p.write_text('k = "[not, an, array]"\n', encoding="utf-8")
        data = _load_toml_fallback(p)
        assert data["k"] == "[not, an, array]"


class TestSaveApiKeyDedup:
    def test_save_collapses_duplicate_sections(self, tmp_path, monkeypatch) -> None:
        import zall.safety.config as sc
        monkeypatch.setattr(sc, "CONFIG_DIR", tmp_path)
        (tmp_path / "config.toml").write_text(_CORRUPT, encoding="utf-8")

        p = sc.save_api_key("sk-NEW")
        text = p.read_text(encoding="utf-8")
        # self-heal: exactly one [auth] + one [model]
        assert _count(text, "[auth]") == 1
        assert _count(text, "[model]") == 1
        # counterexample: no leftover duplicates
        assert _count(text, "[auth]") != 3
        # new key persisted, model preserved (last-wins from parse)
        cfg = sc.load_config()
        assert cfg["api_key"] == "sk-NEW"
        assert cfg["model"] == "agnes-2.0-flash"


class TestPersistModelDedup:
    def test_persist_collapses_duplicate_sections(self, tmp_path, monkeypatch) -> None:
        import zall.safety.config as sc
        from zall.cli import config as cli_config
        monkeypatch.setattr(sc, "CONFIG_DIR", tmp_path)
        (tmp_path / "config.toml").write_text(_CORRUPT, encoding="utf-8")

        cli_config._persist_model_to_config("agnes-2.0-flash")
        text = (tmp_path / "config.toml").read_text(encoding="utf-8")
        assert _count(text, "[auth]") == 1
        assert _count(text, "[model]") == 1
        # api_key preserved through the rewrite (was non-empty)
        cfg = sc.load_config()
        assert cfg["api_key"] == "sk-LAST"
        assert cfg["model"] == "agnes-2.0-flash"
