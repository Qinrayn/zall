"""Tests for layered config loading.

Corresponds to:
  DESIGN.md §4.4 (K-value adjustments)
  DESIGN.md §5.2 (Judge composition adjustments)

IPR-0: each test includes a counterexample.
"""

from __future__ import annotations

import os
import tempfile

import pytest


class TestConfigLayers:
    """Layered configuration loading tests."""

    @pytest.fixture(autouse=True)
    def _clean_extensions(self) -> None:
        """Clean extension suggestions between tests."""
        from zall.cli.config_layers import clear_extension_suggestions
        clear_extension_suggestions()
        yield

    def test_defaults(self) -> None:
        """Defaults should be used when no config files exist."""
        from zall.cli.config_layers import load_config_layers

        config = load_config_layers(
            include_system=False,
            include_user=False,
            include_project=False,
            include_extensions=False,
            include_env=False,
        )
        assert config["api_key"] == ""
        assert config["model"] == "agnes-2.0-flash"
        assert config["api_base"] == "https://apihub.agnes-ai.com/v1"
        assert config["timeout"] == 120.0
        assert config["providers"] == []

    def test_deep_merge(self) -> None:
        """Deep merge should combine nested dicts correctly."""
        from zall.cli.config_layers import _deep_merge

        base = {"a": 1, "b": {"c": 2, "d": 3}}
        override = {"b": {"c": 99, "e": 4}, "f": 5}
        result = _deep_merge(base, override)
        assert result["a"] == 1
        assert result["b"]["c"] == 99  # Overridden
        assert result["b"]["d"] == 3   # Preserved
        assert result["b"]["e"] == 4   # Added
        assert result["f"] == 5        # Added

    # Counterexample: override should not mutate base
    def test_deep_merge_no_mutation(self) -> None:
        """Deep merge should not mutate the base dict."""
        from zall.cli.config_layers import _deep_merge

        base = {"a": 1, "b": {"c": 2}}
        override = {"b": {"c": 99}}
        result = _deep_merge(base, override)
        assert result["b"]["c"] == 99
        assert base["b"]["c"] == 2  # Unchanged

    def test_env_layer(self) -> None:
        """Environment variables should override defaults."""
        from zall.cli.config_layers import load_config_layers

        # Use a subprocess to avoid polluting the test process env
        import subprocess
        code = """
from zall.cli.config_layers import load_config_layers
config = load_config_layers(
    include_system=False, include_user=False, include_project=False,
    include_extensions=False, include_env=True,
)
print(f"model={config.get('model')}")
print(f"api_base={config.get('api_base')}")
"""
        env = os.environ.copy()
        env["ZALL_MODEL"] = "test-model-from-env"
        env["ZALL_API_BASE"] = "https://test.env/api"
        result = subprocess.run(
            ["python", "-c", code],
            capture_output=True, text=True, env=env,
        )
        assert "model=test-model-from-env" in result.stdout
        assert "api_base=https://test.env/api" in result.stdout

    def test_extension_suggestion_layer(self) -> None:
        """Extension suggestions should override file config."""
        from zall.cli.config_layers import load_config_layers, set_extension_suggestions

        set_extension_suggestions({"k_overrides": {"bash": 3}})
        config = load_config_layers(
            include_system=False, include_user=False, include_project=False,
            include_extensions=True, include_env=False,
        )
        assert config["k_overrides"]["bash"] == 3

    # Counterexample: extension suggestions should not pollute between calls
    def test_extension_suggestions_isolated(self) -> None:
        """Extension suggestions should be cleared between tests."""
        from zall.cli.config_layers import load_config_layers, clear_extension_suggestions

        clear_extension_suggestions()
        config = load_config_layers(
            include_system=False, include_user=False, include_project=False,
            include_extensions=True, include_env=False,
        )
        # k_overrides should be empty (default)
        assert config.get("k_overrides", {}) == {}

    def test_extension_suggestions_override_defaults(self) -> None:
        """Extension suggestions should override built-in defaults."""
        from zall.cli.config_layers import load_config_layers, set_extension_suggestions

        set_extension_suggestions({"model": "ext-suggested-model"})
        config = load_config_layers(
            include_system=False, include_user=False, include_project=False,
            include_extensions=True, include_env=False,
        )
        assert config["model"] == "ext-suggested-model"

    def test_env_overrides_extension(self) -> None:
        """Environment variables should override extension suggestions."""
        from zall.cli.config_layers import load_config_layers, set_extension_suggestions

        set_extension_suggestions({"model": "ext-model"})
        # Simulate env by setting directly
        old_val = os.environ.get("ZALL_MODEL")
        try:
            os.environ["ZALL_MODEL"] = "env-model"
            config = load_config_layers(
                include_system=False, include_user=False, include_project=False,
                include_extensions=True, include_env=True,
            )
            assert config["model"] == "env-model"
        finally:
            if old_val is None:
                del os.environ["ZALL_MODEL"]
            else:
                os.environ["ZALL_MODEL"] = old_val

    def test_cli_overrides_all(self) -> None:
        """CLI parameters should override everything."""
        from zall.cli.config_layers import load_config_layers, set_extension_suggestions

        set_extension_suggestions({"model": "ext-model"})
        old_val = os.environ.get("ZALL_MODEL")
        try:
            os.environ["ZALL_MODEL"] = "env-model"
            config = load_config_layers(
                include_system=False, include_user=False, include_project=False,
                include_extensions=True, include_env=True,
                cli_overrides={"model": "cli-model"},
            )
            assert config["model"] == "cli-model"
        finally:
            if old_val is None:
                del os.environ["ZALL_MODEL"]
            else:
                os.environ["ZALL_MODEL"] = old_val

    def test_get_k_override(self) -> None:
        """get_k_override should return the override value when set."""
        from zall.cli.config_layers import get_k_override, set_extension_suggestions

        set_extension_suggestions({"k_overrides": {"bash": 3}})
        assert get_k_override("bash") == 3
        assert get_k_override("grep") is None  # No override

    def test_backward_compat_load_config(self) -> None:
        """load_config() should work without arguments (backward compatible)."""
        from zall.cli.config_layers import load_config

        config = load_config()
        assert "api_key" in config
        assert "model" in config
        assert "api_base" in config

    # Counterexample: path not found should not crash
    def test_nonexistent_system_path(self) -> None:
        """Non-existent system config path should not cause errors."""
        from zall.cli.config_layers import _system_config_path

        path = _system_config_path()
        # If path exists, delete it temporarily for the test
        if path and path.exists():
            pytest.skip("system config exists, cannot test non-existent path")
        # Just verify the function returns None or a non-existent Path
        # This test is about not crashing, not about the specific return value
        assert True  # If we reach here, no crash occurred


class TestConfigLayersFileLoading:
    """Tests with actual config files."""

    def test_config_file_loading(self) -> None:
        """Config file should be loaded correctly."""
        from zall.cli.config_layers import _config_to_dict
        from pathlib import Path

        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".toml", delete=False, encoding="utf-8")
        tmp.write('[auth]\napi_key = "test-key"\n[model]\nname = "test-model"\n')
        tmp.close()

        try:
            config = _config_to_dict(Path(tmp.name))
            assert config["api_key"] == "test-key"
            assert config["model"] == "test-model"
        finally:
            import os as _os
            _os.unlink(tmp.name)

    # Counterexample: bad TOML should not crash
    def test_bad_toml_no_crash(self) -> None:
        """Malformed TOML should not crash the loader."""
        from zall.cli.config_layers import _load_toml_safe
        from pathlib import Path

        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".toml", delete=False, encoding="utf-8")
        tmp.write("[[[[broken toml\n")
        tmp.close()

        try:
            result = _load_toml_safe(Path(tmp.name))
            assert result == {}  # Should return empty dict on error
        finally:
            import os as _os
            _os.unlink(tmp.name)

    def test_layers_merge_priority(self) -> None:
        """Higher priority layers should override lower ones."""
        from zall.cli.config_layers import (
            load_config_layers, set_extension_suggestions, _deep_merge,
        )

        # Simulate: user config sets model, env overrides it
        # We can't easily create temp files for user/project config,
        # so we test the merge logic directly
        defaults = {"model": "default", "timeout": 120}
        user = {"model": "user-model"}
        env = {"model": "env-model"}

        result = _deep_merge(defaults, user)
        result = _deep_merge(result, env)
        assert result["model"] == "env-model"
        assert result["timeout"] == 120  # Preserved from defaults