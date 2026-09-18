"""zall config loader — ~/.zall/config.toml + env vars.

Config priority (high→low):
  1. Explicit function parameter
  2. Environment variable (ZALL_API_KEY, ZALL_MODEL, ZALL_API_BASE)
  3. ~/.zall/config.toml (user-level)
  4. .zall/config.toml (project-level)
  5. Built-in defaults
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from zall._util.toml import extract_section_name
from zall._util.toml import load_toml_simple as _load_toml_simple
from zall._util.win32 import resolve_home_dir

# v2 fix: Windows 中文用户名时 Path.home() 可能因encoding问题returnerrorpath,
# 增加 USERPROFILE 环境variablefallback, 确保能找到 ~/.zall/config.toml
_home = resolve_home_dir()
CONFIG_DIR = _home / ".zall"
DEFAULT_API_BASE = "https://apihub.agnes-ai.com/v1"
# v2 fix: defaultmodel改为 agnes-2.0-flash (API 实际可用的model)
# 旧值 agnes-2.5-flash 在 API 上报 503 model_not_found
DEFAULT_MODEL = "agnes-2.0-flash"

# _load_toml_simple → use zall._util.toml.load_toml_simple (shared across codebase)
# _load_toml_fallback removed in v0.2.2: consolidated into _util/toml.py


def ensure_config() -> Path:
    """Create ~/.zall/ directory and default config if not exists."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    config_path = CONFIG_DIR / "config.toml"
    if not config_path.exists():
        config_path.write_text(
            "# zall config\n"
            "# You can get your API key from https://apihub.agnes-ai.com\n"
            "[auth]\n"
            'api_key = "your-api-key-here"\n'
            "\n"
            "[model]\n"
            'name = "agnes-2.0-flash"\n'
            'api_base = "https://apihub.agnes-ai.com/v1"\n'
            '# timeout = 300  # API 请求超时(秒), 默认 120\n',
            encoding="utf-8",
        )
    return config_path


def load_config() -> dict[str, Any]:
    """Load config from files + env, return merged dict."""
    config: dict[str, Any] = {
        "api_key": "", "model": DEFAULT_MODEL, "api_base": DEFAULT_API_BASE,
        "timeout": 120.0, "providers": [], "provider": "",
        # 多供应商: [keys].<provider> = "sk-..." — 一家一个 key, 切换时各用各的
        # (通用 [auth].api_key 仍是默认/兜底, 见 cli.model_switch.provider_endpoint)
        "provider_keys": {},
        # F2a: 采样参数 + 上下文窗口 (None = 未设置, 不发送给 API)
        "temperature": None, "max_tokens": None, "top_p": None,
        "reasoning_effort": None, "window_size": None,
    }

    def _merge_model_section(data: dict[str, Any]) -> None:
        """从 [model] 段读取采样参数 + window_size (F2a)."""
        if "model" not in data:
            return
        m = data["model"]
        for k in ("temperature", "max_tokens", "top_p",
                  "reasoning_effort", "window_size"):
            v = m.get(k)
            if v is not None:
                # 数值字段转类型; 字符串字段原样
                if k in ("temperature", "top_p"):
                    try:
                        config[k] = float(v)
                    except (ValueError, TypeError):
                        pass
                elif k in ("max_tokens", "window_size"):
                    try:
                        config[k] = int(v)
                    except (ValueError, TypeError):
                        pass
                else:  # reasoning_effort
                    config[k] = str(v).strip().lower() or None

    # 1. User-level config (lower priority, loaded first)
    user_cfg = CONFIG_DIR / "config.toml"
    if user_cfg.exists():
        data = _load_toml_simple(user_cfg)
        if "auth" in data:
            config["api_key"] = data["auth"].get("api_key", config["api_key"])
        if "model" in data:
            config["model"] = data["model"].get("name", config["model"])
            config["api_base"] = data["model"].get("api_base", config["api_base"])
            config["timeout"] = float(data["model"].get("timeout", config["timeout"]))
            # 通用接入: [model] 内可直写 provider (显式路由) + api_key (一处配齐 apikey+baseurl+id)
            if data["model"].get("provider"):
                config["provider"] = str(data["model"]["provider"]).strip()
            if data["model"].get("api_key"):
                config["api_key"] = data["model"]["api_key"]
        _merge_model_section(data)
        if "providers" in data:
            config["providers"] = data["providers"]
        if "keys" in data:
            _keys = data.get("keys") or {}
            if isinstance(_keys, dict):
                config["provider_keys"].update(
                    {str(k): str(v) for k, v in _keys.items() if v}
                )

    # 2. Project-level config (overrides user)
    project_cfg = Path.cwd() / ".zall" / "config.toml"
    if project_cfg.exists():
        data = _load_toml_simple(project_cfg)
        if "auth" in data:
            config["api_key"] = data["auth"].get("api_key", config["api_key"])
        if "model" in data:
            config["model"] = data["model"].get("name", config["model"])
            config["api_base"] = data["model"].get("api_base", config["api_base"])
            config["timeout"] = float(data["model"].get("timeout", config["timeout"]))
            # 通用接入: [model] 内可直写 provider (显式路由) + api_key (一处配齐 apikey+baseurl+id)
            if data["model"].get("provider"):
                config["provider"] = str(data["model"]["provider"]).strip()
            if data["model"].get("api_key"):
                config["api_key"] = data["model"]["api_key"]
        _merge_model_section(data)
        if "providers" in data:
            config["providers"] = data["providers"]
        if "keys" in data:
            _keys = data.get("keys") or {}
            if isinstance(_keys, dict):
                config["provider_keys"].update(
                    {str(k): str(v) for k, v in _keys.items() if v}
                )

    # 3. Env vars (override files)
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
    # F2a: 采样参数 env 覆盖
    if os.environ.get("ZALL_TEMPERATURE"):
        try:
            config["temperature"] = float(os.environ["ZALL_TEMPERATURE"])
        except (ValueError, TypeError):
            pass
    if os.environ.get("ZALL_MAX_TOKENS"):
        try:
            config["max_tokens"] = int(os.environ["ZALL_MAX_TOKENS"])
        except (ValueError, TypeError):
            pass
    if os.environ.get("ZALL_TOP_P"):
        try:
            config["top_p"] = float(os.environ["ZALL_TOP_P"])
        except (ValueError, TypeError):
            pass
    if os.environ.get("ZALL_REASONING_EFFORT"):
        val = os.environ["ZALL_REASONING_EFFORT"].strip().lower()
        config["reasoning_effort"] = val or None
    if os.environ.get("ZALL_WINDOW_SIZE"):
        try:
            config["window_size"] = int(os.environ["ZALL_WINDOW_SIZE"])
        except (ValueError, TypeError):
            pass

    return config


def save_api_key(key: str) -> Path:
    """Save API key to user config, create config if needed."""
    ensure_config()
    config_path = CONFIG_DIR / "config.toml"
    data = _load_toml_simple(config_path)
    # Read existing raw lines, update only [auth] and [model], preserve others
    existing_lines = config_path.read_text(encoding="utf-8").splitlines(keepends=True)
    sections: list[tuple[str, list[str]]] = []
    current_section_lines: list[str] = []
    current_section_name: str = ""
    for line in existing_lines:
        stripped = line.strip()
        sec = extract_section_name(stripped)
        if sec is not None:
            sec_name, _ = sec
            if current_section_lines:
                sections.append((current_section_name, current_section_lines))
            current_section_name = sec_name
            current_section_lines = [line]
        else:
            current_section_lines.append(line)
    if current_section_lines:
        sections.append((current_section_name, current_section_lines))

    # Rebuild: update [auth] and [model], keep everything else.
    # 自愈: 只保留第一个 [auth]/[model], 丢弃重复段 (历史 bug 曾产生重复段 →
    # 文件不断膨胀; 见 _persist_model_to_config 同款去重)。
    new_lines: list[str] = []
    has_auth = False
    has_model = False
    for name, lines in sections:
        if name == "auth":
            if has_auth:
                continue  # 去重: 丢弃多余的 [auth] 段
            new_lines.append("[auth]\n")
            new_lines.append(f'api_key = "{key}"\n')
            has_auth = True
        elif name == "model":
            if has_model:
                continue  # 去重: 丢弃多余的 [model] 段
            new_lines.append("[model]\n")
            new_lines.append(f'name = "{data.get("model", {}).get("name", DEFAULT_MODEL)}"\n')
            new_lines.append(f'api_base = "{data.get("model", {}).get("api_base", DEFAULT_API_BASE)}"\n')
            has_model = True
        else:
            new_lines.extend(lines)

    # Append sections if they didn't exist
    if not has_auth:
        new_lines.append("[auth]\n")
        new_lines.append(f'api_key = "{key}"\n')
    if not has_model:
        new_lines.append("[model]\n")
        new_lines.append(f'name = "{DEFAULT_MODEL}"\n')
        new_lines.append(f'api_base = "{DEFAULT_API_BASE}"\n')

    config_path.write_text("".join(new_lines), encoding="utf-8")
    # limitfileauthority (仅 owner 读写)
    try:
        config_path.chmod(0o600)
        CONFIG_DIR.chmod(0o700)
    except OSError:
        pass
    # Windows ACL: 用 icacls limit仅当前用户可访问
    from zall._util.win32 import restrict_file_acls
    restrict_file_acls(config_path)
    return config_path