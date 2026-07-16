"""zall.cli.config_layers — Layered configuration loading.

Inspired by Grok Build's 6-layer config merge. Adds system-level config
and extension suggestion layers to the existing config pipeline.

Priority chain (low → high):
  ① Built-in defaults
  ② System-level /etc/zall/config.toml (POSIX) / %PROGRAMDATA%/zall/config.toml (Windows)
  ③ User-level ~/.zall/config.toml
  ④ Project-level .zall/config.toml
  ⑤ Extension suggestions (auto_learn, in-memory)
  ⑥ Environment variables ZALL_*
  ⑦ CLI parameters (highest)

Each layer is deep-merged: nested dicts merge recursively, later layers
override earlier ones. File-not-found errors are silently skipped (fail-closed).

Corresponds to:
  §4.4    K-value table adjustments via config layers
  §5.2    Judge composition adjustments via config layers

IPR constraints:
  IPR-0: invariant tests at tests/test_config_layers.py
  IPR-1: corresponds to DESIGN.md §4.4 + §5.2
  IPR-3: stdlib only, no model SDK
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from zall._util.toml import load_toml_simple
from zall._util.win32 import resolve_home_dir


# ── Path helpers ──

def _system_config_path() -> Path | None:
    """Return the system-level config path for the current platform."""
    if os.name == "nt":
        prog_data = os.environ.get("PROGRAMDATA", "")
        if prog_data:
            return Path(prog_data) / "zall" / "config.toml"
        return None
    else:
        etc_path = Path("/etc/zall/config.toml")
        return etc_path if etc_path.exists() else None


def _user_config_path() -> Path:
    """Return the user-level config path (~/.zall/config.toml)."""
    return resolve_home_dir() / ".zall" / "config.toml"


def _project_config_path() -> Path:
    """Return the project-level config path (.zall/config.toml)."""
    return Path.cwd() / ".zall" / "config.toml"


# ── Layer loading ──

def _load_toml_safe(path: Path | None) -> dict[str, Any]:
    """Load a TOML file, returning empty dict on any error."""
    from zall._util.logging import get_zall_logger

    _logger = get_zall_logger(__name__)
    if path is None or not path.exists():
        return {}
    try:
        data = load_toml_simple(path)
        return data if isinstance(data, dict) else {}
    except Exception as _e:
        _logger.warning("failed to load config %s: %s", path, _e)
        return {}


def _env_to_config() -> dict[str, Any]:
    """Build config dict from ZALL_* environment variables."""
    config: dict[str, Any] = {}
    if os.environ.get("ZALL_API_KEY"):
        config["api_key"] = os.environ["ZALL_API_KEY"]
    if os.environ.get("ZALL_MODEL"):
        config["model"] = os.environ["ZALL_MODEL"]
    if os.environ.get("ZALL_API_BASE"):
        config["api_base"] = os.environ["ZALL_API_BASE"]
    if os.environ.get("ZALL_TIMEOUT"):
        try:
            config["timeout"] = float(os.environ["ZALL_TIMEOUT"])
        except (ValueError, TypeError):
            pass
    return config


def _config_to_dict(cfg_path: Path) -> dict[str, Any]:
    """Convert TOML config sections to flat dict.

    Handles the [auth], [model] section structure used by zall config files.
    """
    data = _load_toml_safe(cfg_path)
    result: dict[str, Any] = {}
    if "auth" in data:
        result["api_key"] = data["auth"].get("api_key", "")
    if "model" in data:
        result["model"] = data["model"].get("name", "")
        result["api_base"] = data["model"].get("api_base", "")
        result["timeout"] = float(data["model"].get("timeout", 120.0))
    if "providers" in data:
        result["providers"] = data["providers"]
    return result


# ── Deep merge ──

def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Deep merge two dicts. override takes precedence.

    Nested dicts merge recursively. Lists and scalars are replaced.
    """
    result = dict(base)
    for key, val in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = _deep_merge(result[key], val)
        else:
            result[key] = val
    return result


# ── Extension suggestion layer ──

# Global: extensions can set this to inject config overrides
_EXTENSION_SUGGESTIONS: dict[str, Any] = {}


def set_extension_suggestions(suggestions: dict[str, Any]) -> None:
    """Set config overrides from extension suggestions (auto_learn, etc.).

    This is the self-evolution channel: extensions analyse session data
    and propose config adjustments (e.g., K values, Judge composition).
    """
    global _EXTENSION_SUGGESTIONS
    _EXTENSION_SUGGESTIONS = dict(suggestions)


def clear_extension_suggestions() -> None:
    """Clear extension suggestions (e.g., on /reload)."""
    global _EXTENSION_SUGGESTIONS
    _EXTENSION_SUGGESTIONS = {}


# ── Main API ──

DEFAULTS: dict[str, Any] = {
    "api_key": "",
    "model": "agnes-2.0-flash",
    "api_base": "https://apihub.agnes-ai.com/v1",
    "timeout": 120.0,
    "providers": [],
    "k_overrides": {},
}


def load_config_layers(
    *,
    include_system: bool = True,
    include_user: bool = True,
    include_project: bool = True,
    include_extensions: bool = True,
    include_env: bool = True,
    cli_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Load configuration from all layers, returning merged result.

    Args:
        include_system: Include system-level config (/etc/zall/)
        include_user: Include user-level config (~/.zall/)
        include_project: Include project-level config (.zall/)
        include_extensions: Include extension suggestions
        include_env: Include environment variables
        cli_overrides: CLI parameter overrides (highest priority)

    Returns:
        Merged config dict with all layers.
    """
    layers: list[dict[str, Any]] = []

    # ① Defaults
    layers.append(dict(DEFAULTS))

    # ② System-level (lowest file priority)
    if include_system:
        sys_path = _system_config_path()
        if sys_path is not None:
            layers.append(_config_to_dict(sys_path))

    # ③ User-level
    if include_user:
        user_path = _user_config_path()
        layers.append(_config_to_dict(user_path))

    # ④ Project-level
    if include_project:
        proj_path = _project_config_path()
        layers.append(_config_to_dict(proj_path))

    # ⑤ Extension suggestions
    if include_extensions and _EXTENSION_SUGGESTIONS:
        layers.append(dict(_EXTENSION_SUGGESTIONS))

    # ⑥ Environment variables
    if include_env:
        layers.append(_env_to_config())

    # ⑦ CLI overrides
    if cli_overrides:
        layers.append(dict(cli_overrides))

    # Merge all layers
    result: dict[str, Any] = {}
    for layer in layers:
        result = _deep_merge(result, layer)

    return result


def load_config() -> dict[str, Any]:
    """Backward-compatible wrapper: behaves like safety.config.load_config().

    Returns the same flat dict format as the original load_config().
    """
    return load_config_layers()


def get_k_override(tool_id: str) -> int | None:
    """Get K-value override for a tool from extension suggestions.

    Returns None if no override is set.
    """
    config = load_config_layers(include_extensions=True)
    k_overrides = config.get("k_overrides", {})
    if isinstance(k_overrides, dict):
        val = k_overrides.get(tool_id)
        if isinstance(val, (int, float)):
            return int(val)
    return None