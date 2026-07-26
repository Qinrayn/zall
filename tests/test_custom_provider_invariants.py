"""Tests for custom model provider first-class support (Part A, provider 一等化).

Corresponds to:
  MASTER.md §8 生态建设 - 自定义 provider 应为一等公民
  A1-A4 fixes: get_model_provider/list_providers/get_provider_default_model/
               get_provider_tag 应识别自定义 provider; window/price 应可从 TOML 注入。

IPR-0: each test includes a counterexample - asserts the OLD buggy behavior
(neglect of custom providers) does NOT hold.
"""

from __future__ import annotations

import pytest


# ── Build a synthetic merged registry with a custom provider ──

def _synthetic_registry() -> dict:
    """A merged-style registry with a custom 'sensenova' provider.

    Mimics the output of _merge_custom_providers() but without touching disk,
    so tests are hermetic.
    """
    from zall._util.model_registry import _PROVIDER_REGISTRY
    reg = dict(_PROVIDER_REGISTRY)
    reg["sensenova"] = (
        "SenseNova",                # display
        "ZALL_API_KEY",             # env_key
        "https://token.sensenova.cn/v1",  # api_base
        "https://token.sensenova.cn",     # key_url
        ("deepseek-v4-flash",),     # prefixes -- collides with builtin deepseek!
        "zall.adapters.openai_compat:OpenAICompatAdapter",
    )
    return reg


class TestCustomProviderResolution:
    """A1: get_model_provider must respect custom providers in a merged registry."""

    def test_custom_prefix_resolves(self) -> None:
        """A custom provider's model resolves to that provider, not 'openai'."""
        from zall._util.model_registry import get_model_provider
        reg = _synthetic_registry()
        # Counterexample: builtin-only lookup returns 'deepseek' (wrong); merged
        # lookup must return 'sensenova'.
        assert get_model_provider("deepseek-v4-flash", registry=reg) == "sensenova"

    def test_builtin_still_resolves_without_registry(self) -> None:
        """Passing no registry keeps builtin behavior (backward compat)."""
        from zall._util.model_registry import get_model_provider
        assert get_model_provider("gpt-4o") == "openai"
        assert get_model_provider("deepseek-chat") == "deepseek"

    def test_unknown_model_falls_back_to_openai(self) -> None:
        from zall._util.model_registry import get_model_provider
        reg = _synthetic_registry()
        assert get_model_provider("totally-unknown-model", registry=reg) == "openai"


class TestCustomProviderTag:
    """A3: get_provider_tag must not return '?' for custom providers."""

    def test_custom_provider_tag_is_not_question(self) -> None:
        """A custom provider gets a tag derived from its name, not '?'."""
        from zall._util.model_registry import get_provider_tag
        # Counterexample: old code returned '?' for any unknown provider.
        assert get_provider_tag("sensenova") != "?"
        assert get_provider_tag("sensenova") == "S"  # first letter uppercased

    def test_builtin_tags_preserved(self) -> None:
        from zall._util.model_registry import get_provider_tag
        assert get_provider_tag("openai") == "O"
        assert get_provider_tag("deepseek") == "D"

    def test_empty_provider_returns_question(self) -> None:
        from zall._util.model_registry import get_provider_tag
        assert get_provider_tag("") == "?"


class TestCustomProviderListing:
    """A3: list_providers / get_provider_default_model must include custom providers."""

    def test_list_providers_includes_custom(self) -> None:
        from zall._util.model_registry import list_providers
        reg = _synthetic_registry()
        keys = [k for k, *_ in list_providers(reg)]
        # Counterexample: builtin-only list omits 'sensenova'.
        assert "sensenova" in keys

    def test_default_model_uses_custom_prefix_fallback(self) -> None:
        """A custom provider with no preset gets its first prefix as default model."""
        from zall._util.model_registry import get_provider_default_model
        reg = _synthetic_registry()
        result = get_provider_default_model("sensenova", registry=reg)
        # Counterexample: old code returned "" for any provider lacking a preset.
        assert result != ""
        assert result == "deepseek-v4-flash"  # prefix with trailing '-' stripped

    def test_builtin_default_model_unchanged(self) -> None:
        from zall._util.model_registry import get_provider_default_model
        assert get_provider_default_model("openai") == "gpt-4o-mini"
        assert get_provider_default_model("deepseek") == "deepseek-chat"


class TestCustomWindowPriceOverride:
    """A2: window/price metadata must be injectable for custom providers."""

    def test_custom_window_takes_priority(self) -> None:
        """A custom model's window comes from the override table, not default 32000."""
        from zall._util.model_registry import (
            get_window_size, set_custom_windows, _CUSTOM_WINDOWS,
        )
        set_custom_windows({"deepseek-v4-flash": 128000})
        try:
            # Counterexample: without override, unknown model -> default 32000.
            assert get_window_size("deepseek-v4-flash") == 128000
            assert get_window_size("deepseek-v4-flash") != 32000
        finally:
            set_custom_windows({})  # cleanup

    def test_custom_price_takes_priority(self) -> None:
        """A custom model's price comes from the override table, not default $3/$15."""
        from zall._util.model_registry import (
            get_price, set_custom_prices,
        )
        set_custom_prices({"deepseek-v4-flash": (0.14, 0.28)})
        try:
            pin, pout = get_price("deepseek-v4-flash")
            # Counterexample: without override, unknown model -> ($3.0, $15.0).
            assert (pin, pout) == (0.14, 0.28)
            assert (pin, pout) != (3.0, 15.0)
        finally:
            set_custom_prices({})  # cleanup

    def test_override_does_not_pollute_builtin(self) -> None:
        """Setting a custom override must not change builtin model lookups."""
        from zall._util.model_registry import get_window_size, set_custom_windows
        set_custom_windows({"deepseek-v4-flash": 128000})
        try:
            # gpt-4o is builtin and unaffected by the custom override.
            assert get_window_size("gpt-4o") == 128000
        finally:
            set_custom_windows({})


class TestMergeCustomProvidersInjectsMetadata:
    """A2 end-to-end: _merge_custom_providers injects window/price from a TOML-like config."""

    def test_merge_populates_custom_windows_and_prices(self, tmp_path, monkeypatch) -> None:
        """A [[providers]] block with window_size/price is reflected in the registry."""
        import os
        # Write a minimal config.toml with a custom provider carrying metadata.
        # _merge_custom_providers reads Path.home()/".zall"/config.toml, so the
        # file must live at <home>/.zall/config.toml.
        zall_dir = tmp_path / ".zall"
        zall_dir.mkdir(parents=True, exist_ok=True)
        cfg = zall_dir / "config.toml"
        cfg.write_text(
            '[[providers]]\n'
            'name = "sensenova"\n'
            'display = "SenseNova"\n'
            'adapter = "openai-compat"\n'
            'env_key = "ZALL_API_KEY"\n'
            'api_base = "https://token.sensenova.cn/v1"\n'
            'key_url = "https://token.sensenova.cn"\n'
            'model_prefixes = ["deepseek-v4-flash"]\n'
            'window_size = 128000\n'
            'price_in = 0.14\n'
            'price_out = 0.28\n',
            encoding="utf-8",
        )
        # Point HOME/USERPROFILE at tmp_path so Path.home() resolves to our file.
        # Windows Path.home() prefers USERPROFILE; POSIX prefers HOME. Set both.
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("USERPROFILE", str(tmp_path))

        # Clear the lru_cache so the merge re-reads the new config.
        from zall.cli.config import _clear_provider_registry_cache
        _clear_provider_registry_cache()
        try:
            from zall.cli.config import _get_provider_registry
            reg = _get_provider_registry()
            assert "sensenova" in reg  # custom provider merged

            from zall._util.model_registry import get_window_size, get_price
            # Counterexample: before A2, these returned defaults.
            assert get_window_size("deepseek-v4-flash") == 128000
            assert get_price("deepseek-v4-flash") == (0.14, 0.28)
        finally:
            _clear_provider_registry_cache()
            # Cleanup injected overrides to avoid leaking into other tests.
            from zall._util.model_registry import set_custom_windows, set_custom_prices
            set_custom_windows({})
            set_custom_prices({})
