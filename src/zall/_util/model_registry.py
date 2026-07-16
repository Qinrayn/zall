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


def get_window_size(model_name: str) -> int:
    """查modelwindow大小。已知modelreturn精确值, 未知return保守default值。"""
    if not model_name:
        return _DEFAULT_WINDOW
    # B1 fix: 先精确匹配完整名称
    if model_name in _KNOWN_WINDOWS:
        return _KNOWN_WINDOWS[model_name]
    # 再按前缀匹配, 按名称长度降序 (长前缀优先, 防 gpt-4o-mini 误配 gpt-4o)
    for known, size in _SORTED_WINDOWS:
        if model_name.startswith(known):
            return size
    return _DEFAULT_WINDOW


def get_price(model_name: str) -> tuple[float, float]:
    """获取model价格 (input_price, output_price) $/1M tokens。

    未知模型返回保守默认值 ($3/$15 per 1M)。
    B1 fix: 先精确匹配完整名称, 再按长前缀降序匹配。
    """
    if not model_name:
        return (_DEFAULT_PRICE_IN, _DEFAULT_PRICE_OUT)
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


def get_model_provider(model_name: str) -> str:
    """根据model名推断 provider type (Item B: 基于register表)。"""
    if not model_name:
        return "openai"
    model_lower = model_name.lower()
    for provider, (_display, _env, _base, _url, prefixes, _adapter) in _PROVIDER_REGISTRY.items():
        for prefix in prefixes:
            if model_lower.startswith(prefix):
                return provider
    return "openai"  # default


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