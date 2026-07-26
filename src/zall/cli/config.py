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
    from zall._util.logging import get_zall_logger
    from zall.safety.config import load_config

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
    """First-run onboarding: guided 3-field setup (base URL + model id + key).

    任意 OpenAI-兼容来源只需这 3 项即可接入。均可回车跳过 (保留当前值)。
    """
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
    cur_base = (status.get("api_base") or "").strip()
    cur_model = (status.get("model") or "").strip()
    out.write("  Welcome to zall — connect a model (works with any OpenAI-compatible API).\n")
    out.write("  Three things: base URL, model id, API key. Press Enter to keep the default.\n")
    out.write(f"  (Get a key at {key_url}, or set ZALL_API_KEY.)\n")
    try:
        base = (input_fn(f"  1) base URL [{cur_base or 'default'}]: ") or "").strip()
        model = (input_fn(f"  2) model id [{cur_model or 'default'}]: ") or "").strip()
        key = (input_fn("  3) API key (Enter to skip): ") or "").strip()
    except (EOFError, KeyboardInterrupt):
        out.write("\n")
        return
    # 持久化顺序: model (写 name + 推断 base) → base (用户显式 base 覆盖) → key
    if model:
        _persist_model_to_config(model)
        out.write(f"  \u2713 model = {model}\n")
    if base:
        from zall.cli.commands.config import _persist_config_key
        _persist_config_key("api_base", base)
        out.write(f"  \u2713 api_base = {base}\n")
    if key:
        from zall.safety.config import save_api_key

        save_api_key(key)
        out.write("  \u2713 API key saved to ~/.zall/config.toml\n")
    elif not (model or base):
        out.write("  (skipped — edit ~/.zall/config.toml later, or run /config guide)\n")
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

    # 显式 [model].provider (通用接入: 强制 provider/adapter, 绕过前缀推断。
    # 使任意来源只需 provider + api_base + api_key 即可接入, 不依赖模型名前缀匹配。)
    try:
        from zall.safety.config import load_config as _lc
        cp = (_lc().get("provider") or "").strip().lower()
        if cp in registry:
            return cp
    except Exception:
        pass

    mn = model_name or ""
    if mn:
        # A1 fix: 传入合并表 (含自定义 provider), 使自定义 provider 的 prefix
        # 也参与推断。否则 "deepseek-v4-flash" 会因内置 deepseek prefix 错路由。
        p = get_model_provider(mn, registry=registry)
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
    F2b: 从 load_config() 取采样参数 (temperature/max_tokens/top_p/reasoning_effort)
    经 **extra_kwargs 传给 adapter (该通路本就存在, 此前没人填)。调用方显式传入的
    extra_kwargs 优先于 config 中的值。

    G15 (E2E 设施):
      - model "scripted:<path.json>" 或 env ZALL_SCRIPT → ScriptedAdapter 回放 (不走网络);
      - env ZALL_CHAOS=<0..1> → ChaosAdapter 包装构建结果 (故障注入,
        ZALL_CHAOS_MODES=429,500,transport 可选)。
    """
    import importlib

    # G15: 脚本回放 provider (确定性回归/无 key 冒烟), 优先于一切
    script_path = None
    if model and model.startswith("scripted:"):
        script_path = model.split(":", 1)[1]
    elif os.environ.get("ZALL_SCRIPT"):
        script_path = os.environ["ZALL_SCRIPT"]
    if script_path:
        from zall.adapters.scripted import ScriptedAdapter
        return ScriptedAdapter.from_file(script_path)

    registry = _get_provider_registry()
    entry = registry.get(provider)
    if entry is not None:
        import_path = entry[5]  # adapter_import_path
        module_path, class_name = import_path.split(":")
        module = importlib.import_module(module_path)
        cls = getattr(module, class_name)
    else:
        # 未知 provider -> fallback 到 OpenAI compatible
        from zall.adapters import OpenAICompatAdapter
        cls = OpenAICompatAdapter
    kwargs: dict[str, Any] = {"model": model}
    if timeout is not None:
        kwargs["timeout"] = timeout
    # F2b: 从 config 注入采样参数 (调用方显式传入的 extra_kwargs 优先)
    try:
        from zall.safety.config import load_config as _load_cfg
        cfg = _load_cfg()
        for k in ("temperature", "max_tokens", "top_p", "reasoning_effort"):
            if k not in extra_kwargs:  # 调用方显式值优先
                v = cfg.get(k)
                if v is not None:
                    extra_kwargs[k] = v
    except Exception:
        pass  # config 读取失败不阻塞 adapter 构建
    kwargs.update(extra_kwargs)
    adapter = cls(**kwargs)

    # G15: 故障注入包装 (错误恢复链路的真实会话检验)
    chaos_p = os.environ.get("ZALL_CHAOS", "").strip()
    if chaos_p:
        try:
            from zall.adapters.chaos import ChaosAdapter
            modes_env = os.environ.get("ZALL_CHAOS_MODES", "").strip()
            chaos_kwargs: dict[str, Any] = {"probability": float(chaos_p)}
            if modes_env:
                chaos_kwargs["modes"] = tuple(
                    m.strip() for m in modes_env.split(",") if m.strip()
                )
            adapter = ChaosAdapter(adapter, **chaos_kwargs)
        except (ValueError, TypeError) as e:
            import sys
            print(f"  ⚠ ZALL_CHAOS 无效, 已忽略: {e}", file=sys.stderr)
    return adapter


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
        # A2: 可选元数据 (provider 一等化) - 不填则用默认 window/价格
        window_size = 128000
        price_in = 0.14            # $/1M input tokens
        price_out = 0.28           # $/1M output tokens

    返回合并后的完整注册表 dict (不修改原 _PROVIDER_REGISTRY)。

    A2: 同时把 window_size/price_in/price_out 注入 model_registry 的运行时覆盖表,
    使自定义模型的 get_window_size()/get_price() 返回真实值而非默认 32000/$3+$15。
    """
    registry = dict(_PROVIDER_REGISTRY)  # 浅拷贝内置注册表
    custom_windows: dict[str, int] = {}
    custom_prices: dict[str, tuple[float, float]] = {}
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
            # A2: 收集可选 window/price 元数据, 按 prefix 注册 (前缀匹配)
            ws = entry.get("window_size")
            if isinstance(ws, (int, float)) and ws > 0:
                for pfx in prefixes:
                    custom_windows[pfx] = int(ws)
            pi = entry.get("price_in")
            po = entry.get("price_out")
            if isinstance(pi, (int, float)) and isinstance(po, (int, float)):
                for pfx in prefixes:
                    custom_prices[pfx] = (float(pi), float(po))
    except Exception:
        pass  # 自定义 provider 加载失败不应阻塞启动
    # A2: 注入运行时覆盖表 (即使 custom_* 为空也清空旧值, 保持与配置一致)
    try:
        from zall._util.model_registry import set_custom_prices, set_custom_windows
        set_custom_windows(custom_windows)
        set_custom_prices(custom_prices)
        # F2c: [model].window_size 覆盖 -- 按当前模型名精确匹配注入,
        # 使 compactor 的 get_window_size(model_name) 返回用户设置的真实值
        # (而非硬编码 _KNOWN_WINDOWS 的猜测或默认 32000)。
        try:
            from zall.safety.config import load_config as _load_cfg
            _cfg = _load_cfg()
            _ws = _cfg.get("window_size")
            _mdl = (_cfg.get("model") or "").strip()
            if isinstance(_ws, (int, float)) and _ws > 0 and _mdl:
                # 精确匹配当前模型名 (优先级最高, 因 get_window_size 先查 _CUSTOM_WINDOWS)
                _merged = dict(custom_windows)
                _merged[_mdl] = int(_ws)
                set_custom_windows(_merged)
        except Exception:
            pass
    except Exception:
        pass
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
    from zall._util.toml import load_toml_simple as _load_toml_simple
    from zall.safety.config import CONFIG_DIR
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

            def _emit_kv(k: str, v: Any) -> str:
                """渲染一行 key = value (字符串加引号, 其他原样)。"""
                if isinstance(v, str):
                    return f'{k} = "{v}"\n'
                return f'{k} = {v}\n'

            # 修复根因: 旧版 _update_key_in_lines(lines, ...) 收到含段头的 lines 并
            # 重新吐出段头, 而调用方又单独 append 了段头 → 每次 /model -p 都使
            # [auth]/[model] 段头翻倍 (model 还因两次调用而三倍) → config 不断膨胀。
            # 现改为规范化输出: 段头只写一次, 已知 key 从解析后的 data (last-wins)
            # 取, 额外 key (如 timeout) 保留; 同名段去重 (自愈历史损坏)。
            for name, lines in sections:
                # B4: 只匹配顶级段名 (auth/model), 不匹配 nested.table
                _top = name.split(".")[0].strip() if "." in name else name.strip()
                if _top == "auth":
                    if has_auth:
                        continue  # 去重: 丢弃多余的 [auth] 段 (自愈历史损坏)
                    new_lines.append("[auth]\n")
                    # 只在 api_key 非空时写入, 防空 key 覆盖有效 key
                    if api_key:
                        new_lines.append(_emit_kv("api_key", api_key))
                    # 保留 [auth] 内其他 key (非 api_key)
                    for k, v in auth.items():
                        if k != "api_key":
                            new_lines.append(_emit_kv(k, v))
                    has_auth = True
                elif _top == "model":
                    if has_model:
                        continue  # 去重: 丢弃多余的 [model] 段 (自愈历史损坏)
                    model_cfg = data.get("model", {})
                    api_base = model_cfg.get("api_base", default_api_base)
                    new_lines.append("[model]\n")
                    new_lines.append(_emit_kv("name", model_name))
                    new_lines.append(_emit_kv("api_base", api_base))
                    # 保留 [model] 内其他 key (如 timeout / max_tokens)
                    for k, v in model_cfg.items():
                        if k not in ("name", "api_base"):
                            new_lines.append(_emit_kv(k, v))
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