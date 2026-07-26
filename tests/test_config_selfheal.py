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
