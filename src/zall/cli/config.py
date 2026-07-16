"""zall CLI config helpers — provider detection, adapter building, model aliases.

Extracted from cli/app.py.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from zall._util.model_registry import (
    _ADAPTER_TYPE_MAP,
    _PROVIDER_REGISTRY,
    _provider_api_bases,
    _provider_display,
    _provider_env_vars,
    _provider_key_urls,
    get_model_provider,
)
from zall._util.toml import extract_section_name as _extract_section_name

# ── Constants (built from registry, backwards compatible) ──

_PLACEHOLDER_API_KEY = "your-api-key-here"

_PROVIDER_ENV_VARS: dict[str, str] = dict(_provider_env_vars)
_PROVIDER_DISPLAY: dict[str, str] = dict(_provider_display)
_PROVIDER_DEFAULT_API_BASE: dict[str, str] = dict(_provider_api_bases)
_PROVIDER_GET_KEY_URL: dict[str, str] = dict(_provider_key_urls)

_MODEL_ALIASES: dict[str, str] = {
    # Agnes aliases all point to agnes-2.0-flash (the only active API name)
    "flash": "agnes-2.0-flash",
    "agnes25": "agnes-2.5-flash",
    "agnes": "agnes-2.0-flash",
    "agnes2": "agnes-2.0-flash",
    "agnes-2.0": "agnes-2.0-flash",
    "agnes-2.5": "agnes-2.5-flash",
    "mini": "gpt-4o-mini",
    "4o": "gpt-4o",
    "sonnet": "claude-3-5-sonnet",
    "deepseek": "deepseek-chat",
    "glm": "glm-4-flash",
    "qwen": "qwen-plus",
}


# ── Functions ──


def _config_status() -> dict[str, Any]:
    """Return config readiness status (reused by onboarding / doctor, does not raise)."""
    from zall.safety.config import load_config
    from zall._util.logging import get_zall_logger

    _logger = get_zall_logger(__name__)
    try:
        cfg = load_config()
    except Exception as _e:
        _logger.warning("config load failed, returning defaults: %s", _e)
        cfg = {"api_key": "", "model": "", "api_base": ""}
    api_key = (cfg.get("api_key") or "").strip()
    ready = bool(api_key) and api_key != _PLACEHOLDER_API_KEY
    return {
        "ready": ready,
        "api_key": api_key,
        "model": cfg.get("model") or "",
        "api_base": cfg.get("api_base") or "",
    }


def _infer_provider_from_api_base(api_base: str) -> str:
    """Infer provider from api_base (Item D: based on the full registry)."""
    ab = (api_base or "").lower()
    registry = _get_provider_registry()
    for prov, (_, _, base, _, _, _) in registry.items():
        if base and base.lower() in ab:
            return prov
    # Fallback: keyword matching
    if "agnes" in ab:
        return "agnes"
    if "anthropic" in ab:
        return "anthropic"
    if "generativelanguage" in ab or "googleapis" in ab:
        return "gemini"
    if "deepseek" in ab:
        return "deepseek"
    if "ollama" in ab or "localhost:11434" in ab:
        return "ollama"
    return "openai"


def _default_api_base_for_model(model_name: str) -> str:
    """Infer default api_base from model_name (Item D: based on the full registry)."""
    registry = _get_provider_registry()
    name = (model_name or "").lower()
    if name.startswith("agnes-"):
        info = registry.get("agnes", ("", "", "", "", "", ""))
        if info is not None:
            return info[2] if isinstance(info[2], str) else ""
        return ""
    provider = _detect_provider(model_name)
    info = registry.get(provider)
    if info is not None:
        return info[2] if isinstance(info[2], str) else ""
    return ""


def _onboarding(out: Any, input_fn: Any) -> None:
    """First-run onboarding: interactively configure when API key is missing."""
    status = _config_status()
    if status["ready"]:
        return
    from zall.safety.config import ensure_config

    ensure_config()
    if not hasattr(out, "isatty") or not out.isatty():
        out.write("  ⚠ no API key configured — set ZALL_API_KEY or edit "
                  "~/.zall/config.toml\n")
        out.flush()
        return
    # Infer provider from api_base to show the correct key URL
    provider = _infer_provider_from_api_base(status.get("api_base", ""))
    key_url = _PROVIDER_GET_KEY_URL.get(provider, _PROVIDER_GET_KEY_URL["agnes"])
    out.write("  Welcome to zall — no API key configured yet.\n")
    out.write(f"  Get one at {key_url} (or set ZALL_API_KEY)\n")
    try:
        key = (input_fn("  API key (Enter to skip): ") or "").strip()
    except (EOFError, KeyboardInterrupt):
        out.write("\n")
        return
    if key:
        from zall.safety.config import save_api_key

        save_api_key(key)
        out.write("  ✓ saved to ~/.zall/config.toml\n")
    else:
        out.write("  (skipped — edit ~/.zall/config.toml later, or run /doctor)\n")
    out.flush()


def _resolve_model_alias(name: str) -> str:
    """Short alias to full model name; non-aliases are returned as-is.

    "default" reads the current model from config.toml instead of hardcoding.
    """
    key = name.strip().lower()
    if key == "default":
        from zall.safety.config import load_config
        try:
            cfg = load_config()
            model = (cfg.get("model") or "").strip()
            if model:
                return model
        except Exception:
            pass
    return _MODEL_ALIASES.get(key, name.strip())


def _detect_provider(model_name: str | None = None) -> str:
    """自动检测 provider type (Item D: 基于完整register表, 含自定义 provider)。

    检测优先级:
      1. ZALL_PROVIDER 环境变量
      2. 模型名前缀匹配 (基于 _PROVIDER_REGISTRY + 自定义 provider)
      3. API key 环境变量存在判定
      4. 默认 → openai
    """
    provider = os.environ.get("ZALL_PROVIDER", "").strip().lower()
    registry = _get_provider_registry()
    if provider in registry:
        return provider

    mn = model_name or ""
    if mn:
        p = get_model_provider(mn)
        if p in registry:
            return p

    # check各 provider 的 env var
    for prov, (_, env_var, _, _, _, _) in registry.items():
        if env_var and os.environ.get(env_var):
            return prov

    return "openai"


def _build_adapter(provider: str, model: str | None = None, timeout: float | None = None, **extra_kwargs: Any) -> Any:
    """根据 provider typeconstructcorresponds to adapter (Item D: importlib dynamicload, 零 if/elif)。

    provider 未知时 fallback 到 OpenAICompatAdapter。
    timeout: API 请求超时秒数, None 表示使用 adapter 默认值 (120s)。
    """
    import importlib
    registry = _get_provider_registry()
    entry = registry.get(provider)
    if entry is not None:
        import_path = entry[5]  # adapter_import_path
        module_path, class_name = import_path.split(":")
        module = importlib.import_module(module_path)
        cls = getattr(module, class_name)
    else:
        # 未知 provider → fallback 到 OpenAI compatible
        from zall.adapters import OpenAICompatAdapter
        cls = OpenAICompatAdapter
    kwargs: dict[str, Any] = {"model": model}
    if timeout is not None:
        kwargs["timeout"] = timeout
    # Merge any extra kwargs passed by caller (e.g., temperature, max_tokens)
    kwargs.update(extra_kwargs)
    return cls(**kwargs)


# ──────────────────────────────────────────────────────────────────────────
# Item D: 自定义 Provider merge (从 TOML [[providers]] 段load)
# ──────────────────────────────────────────────────────────────────────────


def _merge_custom_providers() -> dict[str, Any]:
    """从 ~/.zall/config.toml load自定义 provider 并与内置register表merge。

    格式:
        [[providers]]
        name = "my-llm"
        display = "My LLM"
        adapter = "openai-compat"   # 对应 _ADAPTER_TYPE_MAP 中的 key
        env_key = "MY_LLM_KEY"
        api_base = "https://my-llm.example.com/v1"
        key_url = "https://my-llm.example.com/keys"
        model_prefixes = ["my-", "myllm-"]

    返回合并后的完整注册表 dict (不修改原 _PROVIDER_REGISTRY)。
    """
    registry = dict(_PROVIDER_REGISTRY)  # 浅拷贝内置注册表
    try:
        config_path = Path.home() / ".zall" / "config.toml"
        if not config_path.exists():
            config_path = Path.cwd() / ".zall" / "config.toml"
        if not config_path.exists():
            return registry
        from zall._util.toml import load_toml_simple as _load_toml_simple
        data = _load_toml_simple(config_path)
        custom_providers = data.get("providers", [])
        if not custom_providers:
            return registry
        for entry in custom_providers:
            name = entry.get("name")
            if not name or not isinstance(name, str):
                continue
            adapter_type = entry.get("adapter", "openai-compat")
            import_path = _ADAPTER_TYPE_MAP.get(adapter_type)
            if import_path is None:
                continue  # 未知 adapter 类型, 跳过
            display = entry.get("display", name)
            env_key = entry.get("env_key", "")
            api_base = entry.get("api_base", "")
            key_url = entry.get("key_url", "")
            prefixes = tuple(entry.get("model_prefixes", [name]))
            registry[name] = (display, env_key, api_base, key_url, prefixes, import_path)
    except Exception:
        pass  # 自定义 provider 加载失败不应阻塞启动
    return registry


@lru_cache(maxsize=1)
def _get_provider_registry() -> dict[str, Any]:
    """获取完整 provider register表 (内置 + 自定义)。lru_cache cache一次结果。"""
    return _merge_custom_providers()


def _clear_provider_registry_cache() -> None:
    """刷新 provider register表cache (供 /reload 调用)。"""
    _get_provider_registry.cache_clear()


def _persist_model_to_config(model_name: str) -> None:
    """将model名write ~/.zall/config.toml，preserve现有其他段 (fix B1: 不再全量覆写)。"""
    from zall.safety.config import CONFIG_DIR
    from zall._util.toml import load_toml_simple as _load_toml_simple
    config_path = CONFIG_DIR / "config.toml"
    try:
        config_path.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    # v0.1.1 fix: 不再写死 agnes api_base, 根据 model_name 推断default值
    default_api_base = _default_api_base_for_model(model_name)

    if config_path.exists():
        try:
            existing_lines = config_path.read_text(encoding="utf-8").splitlines(keepends=True)
            data = _load_toml_simple(config_path)
        except Exception:
            existing_lines = []
            data = {}

        if existing_lines:
            # 增量更新 [auth] 和 [model] 段，preserve其他段 + 注释
            # B4: 鲁棒的段parse — handle行内注释、nested tables
            sections: list[tuple[str, list[str]]] = []
            current_lines: list[str] = []
            current_section = ""
            for line in existing_lines:
                stripped = line.strip()
                # 检测 TOML section 头: [section] 或 [section] # comment
                # compatible行内注释: 取第一个未reference的 # 之前的content
                if stripped.startswith("["):
                    _extract = _extract_section_name(stripped)
                    if _extract is not None:
                        sec_name, _ = _extract
                        if current_lines:
                            sections.append((current_section, current_lines))
                        current_section = sec_name
                        current_lines = [line]
                        continue
                current_lines.append(line)
            if current_lines:
                sections.append((current_section, current_lines))

            auth = data.get("auth", {})
            api_key = auth.get("api_key", "")
            new_lines: list[str] = []
            has_auth = has_model = False

            # B10 fix: preserve原段内所有 key, 只更新特定 key
            def _update_key_in_lines(lines: list[str], key: str, value: str) -> list[str]:
                """在 section 行list中更新指定 key 的值, preserve注释和sequential。"""
                updated = []
                found = False
                for line in lines:
                    stripped = line.strip()
                    # skip注释行和空行
                    if stripped.startswith("#") or not stripped:
                        updated.append(line)
                        continue
                    # check是否是 key = value 行 (ignore行内注释)
                    eq_pos = stripped.find("=")
                    if eq_pos > 0:
                        k = stripped[:eq_pos].strip()
                        if k == key:
                            # preserve行内注释
                            comment = ""
                            # 找到值结束后的 # 注释
                            # handle引号
                            in_quote = False
                            for ci in range(eq_pos + 1, len(stripped)):
                                ch = stripped[ci]
                                if ch in ('"', "'"):
                                    in_quote = not in_quote
                                elif ch == "#" and not in_quote:
                                    comment = stripped[ci:]
                                    break
                            indent = line[:len(line) - len(line.lstrip())]
                            updated.append(f'{indent}{key} = "{value}"{comment}\n')
                            found = True
                            continue
                    updated.append(line)
                if not found:
                    # key 不存在, 追加到 section 末尾
                    indent = " " * 4
                    updated.append(f'{indent}{key} = "{value}"\n')
                return updated

            for name, lines in sections:
                # B4: 只匹配顶级段名 (auth/model), 不匹配 nested.table
                _top = name.split(".")[0].strip() if "." in name else name.strip()
                if _top == "auth":
                    # Fix: 只在 api_key 非空时写入 [auth] 段，防止空 key 覆盖有效 key
                    if api_key:
                        new_lines.append("[auth]\n")
                        new_lines.extend(_update_key_in_lines(lines, "api_key", api_key))
                        has_auth = True
                    else:
                        # 保留原 [auth] 段内容不变（不更新 api_key）
                        new_lines.append("[auth]\n")
                        for l in lines:
                            if l.strip() and not l.strip().startswith("#") and "=" in l.strip():
                                k = l.strip().split("=", 1)[0].strip()
                                if k == "api_key":
                                    continue  # 跳过空 api_key 行
                            new_lines.append(l)
                        has_auth = True
                elif _top == "model":
                    model_cfg = data.get("model", {})
                    api_base = model_cfg.get("api_base", default_api_base)
                    new_lines.append("[model]\n")
                    new_lines.extend(_update_key_in_lines(lines, "name", model_name))
                    new_lines.extend(_update_key_in_lines(lines, "api_base", api_base))
                    has_model = True
                else:
                    new_lines.extend(lines)
            if not has_auth and api_key:
                new_lines.append("[auth]\n")
                new_lines.append(f'api_key = "{api_key}"\n')
            if not has_model:
                new_lines.append("[model]\n")
                new_lines.append(f'name = "{model_name}"\n')
                new_lines.append(f'api_base = "{default_api_base}"\n')
            config_path.write_text("".join(new_lines), encoding="utf-8")
            return
        # fall through: file存在但无法按段parse → 按新file写

    # file不存在或不可parse → 写新template
    # 新文件也写入 api_key = "" 占位（用户需手动配置）
    config_path.write_text(
        "# zall config\n"
        "[auth]\n"
        'api_key = ""\n'
        "\n"
        "[model]\n"
        f'name = "{model_name}"\n'
        f'api_base = "{default_api_base}"\n',
        encoding="utf-8",
    )


# _extract_section_name moved to zall._util.toml (O3/B5)