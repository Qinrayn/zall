"""zall._util.model_registry — 统一model元数据 (window大小 + 价格 + 别名 + provider register表).

C1: 消除模型元数据分散在两处的问题:
  - compactor.py 的 _KNOWN_WINDOWS (窗口大小)
  - app.py _cmd_cost 的 _PRICES (价格表)
  - cli/config.py 的 _MODEL_PRESETS (别名/预设)

新增模型时只需更新本文件一处。

重命名自 model_meta.py (v0.2.2): 更准确的命名反映其包含 provider 注册表。"""

from __future__ import annotations

# ──────────────────────────────────────────────────────────────────────────
# modelwindow大小 (token)
# ──────────────────────────────────────────────────────────────────────────

_KNOWN_WINDOWS: dict[str, int] = {
    # OpenAI
    "gpt-4o": 128000,
    "gpt-4o-mini": 128000,
    "gpt-4-turbo": 128000,
    "gpt-4.1": 128000,
    "gpt-4.1-mini": 128000,
    "gpt-4.1-nano": 128000,
    "o1": 200000,
    "o3-mini": 200000,
    "o4-mini": 200000,
    # Anthropic
    "claude-3-5-sonnet": 200000,
    "claude-3-5-haiku": 200000,
    "claude-3-opus": 200000,
    "claude-3-haiku": 200000,
    "claude-sonnet-4": 200000,
    "claude-sonnet-4-20250514": 200000,
    # Google
    "gemini-2.5-pro": 1_000_000,
    "gemini-2.5-flash": 1_000_000,
    "gemini-2.0-flash": 1_000_000,
    # DeepSeek
    "deepseek-chat": 128000,
    "deepseek-reasoner": 128000,
    "deepseek-v3": 128000,
    "deepseek-r1": 128000,
    # Meta
    "llama3.1": 128000,
    "llama3": 8192,
    "llama-3": 8192,
    # Qwen (all modern variants 128K)
    "qwen2.5": 128000,
    "qwen2.5-coder": 128000,
    "qwen-plus": 131072,
    "qwen3": 131072,
    # 其他
    "agnes-1.5-flash": 128000,
    "agnes-2.0-flash": 128000,
    "agnes-2.5-flash": 128000,
    "glm-4-flash": 128000,
    "glm-4": 128000,
    # llama.cpp (window大小由启动parameter决定, 写常见值)
    "llama.cpp-local": 8192,
}

# defaultwindow大小 (未知model)
_DEFAULT_WINDOW: int = 32000

# A2 (provider 一等化): 自定义 provider 的 window/price 运行时覆盖表。
# 由 cli/config.py _merge_custom_providers() 在启动时填充 (从 TOML [[providers]] 的
# window_size / price_in / price_out 字段)。get_window_size()/get_price() 先查此表,
# 再查内置 _KNOWN_WINDOWS/_KNOWN_PRICES。这样自定义模型不再一律拿到默认 32000 / $3+$15。
_CUSTOM_WINDOWS: dict[str, int] = {}
_CUSTOM_PRICES: dict[str, tuple[float, float]] = {}

# 上游元数据表 (G7): /model 探测 /models 时顺带抓回的 context_length, 按模型 id
# 存这里。优先级高于内置 _KNOWN_WINDOWS 的猜测: 网关在 /models 里声明的 context 是
# 该模型在当前网关下的**事实**, 而内置表只是常见模型名的近似 (同名模型不同网关
# 可能给的 context 不同, 如 sensenova 网关上 deepseek-v4-flash = 1M)。
# 未探测/不可用网关 (Anthropic 原生 / 失败) → 空表, 查校回退 _KNOWN_WINDOWS。
_LIVE_WINDOWS: dict[str, int] = {}


def set_live_windows(context_map: dict[str, int]) -> None:
    """注入上游 /models 探测到的 context_length 表 (keyed by 模型 id)。

    覆盖语义: 整体替换 — 调用方传的是"本次探测到的一组模型", 旧探测结果
    (网关可能已换) 不应残留。探测失败 → 传空表即可清掉。
    """
    _LIVE_WINDOWS.clear()
    for k, v in (context_map or {}).items():
        if v and v > 0:
            _LIVE_WINDOWS[str(k)] = int(v)


def set_custom_windows(prices_map: dict[str, int]) -> None:
    """供 config 层注入自定义 provider 的 window 元数据 (A2)。"""
    _CUSTOM_WINDOWS.clear()
    _CUSTOM_WINDOWS.update(prices_map)


def set_custom_prices(prices_map: dict[str, tuple[float, float]]) -> None:
    """供 config 层注入自定义 provider 的 price 元数据 (A2)。"""
    _CUSTOM_PRICES.clear()
    _CUSTOM_PRICES.update(prices_map)


# ──────────────────────────────────────────────────────────────────────────
# model价格表 ($/1M tokens)
# ──────────────────────────────────────────────────────────────────────────

_KNOWN_PRICES: dict[str, tuple[float, float]] = {
    # OpenAI
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4-turbo": (10.00, 30.00),
    "gpt-4.1": (2.00, 8.00),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1-nano": (0.10, 0.40),
    "o1": (15.00, 60.00),
    "o3-mini": (1.10, 4.40),
    "o4-mini": (0.15, 0.60),
    # Anthropic
    "claude-3-5-sonnet": (3.00, 15.00),
    "claude-3-5-haiku": (0.80, 4.00),
    "claude-3-opus": (15.00, 75.00),
    "claude-3-haiku": (0.25, 1.25),
    "claude-sonnet-4": (3.00, 15.00),
    "claude-sonnet-4-20250514": (3.00, 15.00),
    # Google
    "gemini-2.5-pro": (1.25, 5.00),
    "gemini-2.5-flash": (0.15, 0.60),
    "gemini-2.0-flash": (0.10, 0.40),
    # DeepSeek
    "deepseek-chat": (0.14, 0.28),
    "deepseek-reasoner": (0.55, 2.19),
    "deepseek-v3": (0.27, 1.10),
    "deepseek-r1": (0.55, 2.19),
    # Qwen
    "qwen2.5": (0.35, 1.20),
    "qwen2.5-coder": (0.35, 1.20),
    "qwen-plus": (0.80, 2.00),
    "qwen3": (0.80, 2.00),
    # Zhipu GLM
    "glm-4-flash": (0.10, 0.10),
    "glm-4": (0.50, 1.50),
    # 本地model / 其他
    "agnes-1.5-flash": (0.15, 0.60),
    "agnes-2.0-flash": (0.15, 0.60),
    "agnes-2.5-flash": (0.15, 0.60),
    "llama3.1": (0.0, 0.0),  # local
    "llama.cpp-local": (0.0, 0.0),  # local (llama.cpp server)
}

# default价格 (未知model用保守值)
_DEFAULT_PRICE_IN: float = 3.0   # $3/M input
_DEFAULT_PRICE_OUT: float = 15.0  # $15/M output

# O8: 模块级预sort (按名称长度降序)，避免每次调 get_window_size/get_price 都 sorted()
_SORTED_WINDOWS: list[tuple[str, int]] = sorted(
    _KNOWN_WINDOWS.items(), key=lambda x: -len(x[0])
)
_SORTED_PRICES: list[tuple[str, tuple[float, float]]] = sorted(
    _KNOWN_PRICES.items(), key=lambda x: -len(x[0])
)


def _sort_live() -> list[tuple[str, int]]:
    """_LIVE_WINDOWS 前缀匹配视图 (按 key 长度降序, 更长的前缀优先)。"""
    return sorted(_LIVE_WINDOWS.items(), key=lambda x: -len(x[0]))


def get_window_size(model_name: str) -> int:
    """查 model 窗口大小。已知 model 返回精确值, 未知返回保守默认值。

    A2: 先查自定义 provider 运行时覆盖表 (_CUSTOM_WINDOWS), 再查内置表。
    G7: 上游 /models 探测到的 context (context_length) 优先于内置常规表 —
        探测结果是该模型在当前网关下的真实上限 (常见反例: sensenova 网关
        上 deepseek-v4-flash 实为 1M, 内置表只写过 128k 旧值)。
    """
    if not model_name:
        return _DEFAULT_WINDOW
    # 自定义 provider 显式配置优先 (A2 契约: 用户配置的是最高优先级 —
    # 是逃生阀, 用户在自己网关上可能想留安全余量, 该覆盖探测值)。
    if model_name in _CUSTOM_WINDOWS:
        return _CUSTOM_WINDOWS[model_name]
    for known, size in sorted(_CUSTOM_WINDOWS.items(), key=lambda x: -len(x[0])):
        if model_name.startswith(known):
            return size
    # G7: 上游真实上限其次 (探测存在时) — 用户没显式配置的模型,
    # 用网关在 /models 里声明的 context_length, 而非内置猜测/默认 32K。
    if model_name in _LIVE_WINDOWS:
        return _LIVE_WINDOWS[model_name]
    for known, size in _sort_live():
        if model_name.startswith(known):
            return size
    # B1 fix: 先精确匹配完整名称
    if model_name in _KNOWN_WINDOWS:
        return _KNOWN_WINDOWS[model_name]
    # 再按前缀匹配, 按名称长度降序 (长前缀优先, 防 gpt-4o-mini 误配 gpt-4o)
    for known, size in _SORTED_WINDOWS:
        if model_name.startswith(known):
            return size
    return _DEFAULT_WINDOW


def window_size_known(model_name: str) -> bool:
    """模型 window 是否**已知** (非默认兜底值 32K)。

    区分两种未知:
      - 已探测/已配置/内置表已知 → 精确值, 展示层可放心显示 window 与百分比
      - 完全未知 (回落 _DEFAULT_WINDOW) → 展示层不应伪造 "32K" 或算百分比,
        只显示已用 token 数 (诚实的原始数据)。
    """
    if not model_name:
        return False
    if model_name in _CUSTOM_WINDOWS:
        return True
    for known, _size in _CUSTOM_WINDOWS.items():
        if model_name.startswith(known):
            return True
    if model_name in _LIVE_WINDOWS:
        return True
    for known, _size in _sort_live():
        if model_name.startswith(known):
            return True
    if model_name in _KNOWN_WINDOWS:
        return True
    for known, _size in _SORTED_WINDOWS:
        if model_name.startswith(known):
            return True
    return False


def get_price(model_name: str) -> tuple[float, float]:
    """获取model价格 (input_price, output_price) $/1M tokens。

    未知模型返回保守默认值 ($3/$15 per 1M)。
    B1 fix: 先精确匹配完整名称, 再按长前缀降序匹配。

    A2: 先查自定义 provider 运行时覆盖表 (_CUSTOM_PRICES), 再查内置表。
    """
    if not model_name:
        return (_DEFAULT_PRICE_IN, _DEFAULT_PRICE_OUT)
    # A2: 自定义 provider 覆盖优先 (精确 + 前缀)
    if model_name in _CUSTOM_PRICES:
        return _CUSTOM_PRICES[model_name]
    for prefix, prices in sorted(_CUSTOM_PRICES.items(), key=lambda x: -len(x[0])):
        if model_name.startswith(prefix):
            return prices
    # 先精确匹配
    if model_name in _KNOWN_PRICES:
        return _KNOWN_PRICES[model_name]
    # 再按前缀长度降序匹配
    for prefix, prices in _SORTED_PRICES:
        if model_name.startswith(prefix):
            return prices
    return (_DEFAULT_PRICE_IN, _DEFAULT_PRICE_OUT)


# ──────────────────────────────────────────────────────────────────────────
# Item B: Provider register表 (统一元数据, 消除 if/elif 链)
# ──────────────────────────────────────────────────────────────────────────

# Item D: 6-tuple: (display, env_var, default_api_base, get_key_url, model_prefixes, adapter_import_path)
# adapter_import_path 格式: "module.path:ClassName" — 供 _build_adapter 用 importlib dynamicload
_ProviderMeta = dict[str, tuple[str, str, str, str, tuple[str, ...], str]]

_PROVIDER_REGISTRY: _ProviderMeta = {
    "openai":    ("OpenAI-compatible",    "ZALL_API_KEY",       "https://api.openai.com/v1",                     "https://platform.openai.com/api-keys",             ("gpt-", "o1", "o3", "o4", "glm-", "qwen"), "zall.adapters.openai_compat:OpenAICompatAdapter"),
    "anthropic": ("Anthropic Claude",     "ANTHROPIC_API_KEY",  "https://api.anthropic.com",                     "https://console.anthropic.com/",                   ("claude-", "claude"),                                "zall.adapters.anthropic:AnthropicAdapter"),
    "gemini":    ("Google Gemini",        "GOOGLE_API_KEY",     "https://generativelanguage.googleapis.com",      "https://aistudio.google.com/app/apikey",           ("gemini-", "gemini"),                                "zall.adapters.gemini:GeminiAdapter"),
    "deepseek":  ("DeepSeek",             "DEEPSEEK_API_KEY",   "https://api.deepseek.com/v1",                   "https://platform.deepseek.com/api_keys",           ("deepseek-", "deepseek"),                            "zall.adapters.openai_compat:OpenAICompatAdapter"),
    "ollama":    ("Ollama (local)",       "",                    "http://localhost:11434",                        "https://ollama.ai",                                ("ollama-", "llama"),                                 "zall.adapters.ollama:OllamaAdapter"),
    "agnes":     ("Agnes AI",             "ZALL_API_KEY",       "https://apihub.agnes-ai.com/v1",                "https://apihub.agnes-ai.com",                      ("agnes-",),                                          "zall.adapters.openai_compat:OpenAICompatAdapter"),
}

# compatible性: preserve旧 dict reference (代码中仍reference _PROVIDER_DISPLAY 等)
_provider_display = {k: v[0] for k, v in _PROVIDER_REGISTRY.items()}
_provider_env_vars = {k: v[1] for k, v in _PROVIDER_REGISTRY.items()}
_provider_api_bases = {k: v[2] for k, v in _PROVIDER_REGISTRY.items()}
_provider_key_urls = {k: v[3] for k, v in _PROVIDER_REGISTRY.items()}

# Item D: 已知 adapter type → import path mapping (供 TOML 自定义 provider 用)
_ADAPTER_TYPE_MAP: dict[str, str] = {
    "openai-compat": "zall.adapters.openai_compat:OpenAICompatAdapter",
    "anthropic":     "zall.adapters.anthropic:AnthropicAdapter",
    "gemini":        "zall.adapters.gemini:GeminiAdapter",
    "ollama":        "zall.adapters.ollama:OllamaAdapter",
}


def get_model_provider(model_name: str, registry: _ProviderMeta | None = None) -> str:
    """根据model名推断 provider type (Item B: 基于register表)。

    A1 fix (provider 一等化): 若传入 registry (合并表, 含自定义 provider) 则用之,
    否则仅用内置 _PROVIDER_REGISTRY。调用方 (cli/config._detect_provider) 传入
    _get_provider_registry() 以使自定义 provider 的 prefix 也参与推断, 否则
    "deepseek-v4-flash" 会因内置 deepseek prefix 错路由, 自定义 provider 永远
    匹配不到。

    A1b fix (最长前缀优先): 当多个 provider 的 prefix 都匹配时 (典型冲突:
    内置 "deepseek-" 与自定义 "deepseek-v4-flash" 同时匹配模型名
    "deepseek-v4-flash"), 选最长匹配的 prefix 对应的 provider。这保证更具体的
    自定义 provider 能覆盖较宽泛的内置 provider, 而不依赖 dict 迭代顺序。
    """
    if not model_name:
        return "openai"
    reg = registry if registry is not None else _PROVIDER_REGISTRY
    model_lower = model_name.lower()
    best_provider: str | None = None
    best_prefix_len: int = -1
    for provider, (_display, _env, _base, _url, prefixes, _adapter) in reg.items():
        for prefix in prefixes:
            if model_lower.startswith(prefix) and len(prefix) > best_prefix_len:
                best_prefix_len = len(prefix)
                best_provider = provider
    return best_provider if best_provider is not None else "openai"  # default


# ──────────────────────────────────────────────────────────────────────────
# model预设list (供交互式 picker 使用, 从 config.py 迁移至此)
# ──────────────────────────────────────────────────────────────────────────

_MODEL_PRESETS: list[tuple[str, str, str, str]] = [
    ("agnes-2.0-flash", "agnes-2.0-flash", "fast / cheap (default + latest)", "agnes"),
    ("agnes-2.5-flash", "agnes-2.5-flash", "fast / cheap (newer)", "agnes"),
    ("agnes-1.5-flash", "agnes-1.5-flash", "fast / cheap (legacy)", "agnes"),
    ("gpt-4o-mini",     "gpt-4o-mini",     "OpenAI, cheap", "openai"),
    ("gpt-4o",          "gpt-4o",          "OpenAI, capable", "openai"),
    ("claude-3-5-sonnet", "claude-3-5-sonnet", "Anthropic", "anthropic"),
    ("claude-sonnet-4", "claude-sonnet-4-20250514", "Anthropic Claude Sonnet 4", "anthropic"),
    ("deepseek-chat",   "deepseek-chat",   "DeepSeek", "deepseek"),
    ("glm-4-flash",     "glm-4-flash",     "Zhipu GLM", "openai"),
    ("qwen-plus",       "qwen-plus",       "Alibaba Qwen", "openai"),
    ("gemini-2.5-pro",  "gemini-2.5-pro-exp-03-25", "Google Gemini 2.5 Pro", "gemini"),
    ("gemini-2.5-flash", "gemini-2.5-flash-001", "Google Gemini 2.5 Flash", "gemini"),
    ("ollama-llama3",   "llama3.1",        "Ollama local (llama3.1)", "ollama"),
    ("ollama-qwen25",   "qwen2.5",         "Ollama local (qwen2.5)", "ollama"),
    ("llama.cpp",       "llama.cpp-local", "llama.cpp local server (OpenAI-compat)", "openai"),
]


# ─────────────────────────────────────────────────────────────
# Provider 展示辅助 (单一真相源, 供 CLI /model /provider 使用, 消除重复硬编码)
# ─────────────────────────────────────────────────────────────

# provider key → 单字母标签 (紧凑列表用)
_PROVIDER_TAG: dict[str, str] = {
    "openai": "O",
    "anthropic": "C",
    "gemini": "G",
    "ollama": "L",
    "agnes": "A",
    "deepseek": "D",
}


def get_provider_display(provider: str) -> str:
    """provider key → 人类可读显示名 (取自 _PROVIDER_REGISTRY, 单一真相源)。"""
    meta = _PROVIDER_REGISTRY.get(provider)
    return meta[0] if meta else provider


def get_provider_tag(provider: str) -> str:
    """provider key -> 单字母标签; 未知 provider (含自定义) 返回其名首字母大写。

    A3 fix (provider 一等化): 自定义 provider 不再固定返回 '?'。用 provider 名
    首字母大写作为兜底标签, 使自定义 provider 在 /model 紧凑列表里也有可区分标记。
    """
    tag = _PROVIDER_TAG.get(provider)
    if tag:
        return tag
    if provider:
        return provider[0].upper()
    return "?"


def list_providers(registry: _ProviderMeta | None = None) -> list[tuple[str, str, str, str]]:
    """列出所有已注册 provider: (key, display, env_var, get_key_url)。

    A3 fix: 默认读合并表 (含自定义 provider)。调用方可传内置 _PROVIDER_REGISTRY
    以仅列内置。
    """
    reg = registry if registry is not None else _PROVIDER_REGISTRY
    return [(key, meta[0], meta[1], meta[3]) for key, meta in reg.items()]


def get_provider_default_model(provider: str, registry: _ProviderMeta | None = None) -> str:
    """provider key -> 该 provider 的默认模型 full_name (_MODEL_PRESETS 首个匹配)。

    无匹配预设时返回空串 (调用方需提示用户手动指定模型)。

    A3 fix: 对自定义 provider 无预设时, 不再固定返回空串 -- 改用其首个 model_prefix
    (若注册表项可取到), 仍无则返回空串。这样 /provider <custom> 不再因无预设而失败。
    """
    reg = registry if registry is not None else _PROVIDER_REGISTRY
    for _alias, full_name, _note, prov in _MODEL_PRESETS:
        if prov == provider:
            return full_name
    # 自定义 provider 兜底: 取其首个 prefix 作默认模型名
    meta = reg.get(provider)
    if meta is not None:
        prefixes = meta[4]
        if prefixes:
            return prefixes[0].rstrip("-")
    return ""