"""zall.cli.model_switch — provider/model 热切换 (切换即生效, 唯一真源)。

为什么需要这个模块 (修复的两个真实缺陷):

  1. `/model X` 只写 state["model"], 运行中的 loop 仍握着旧 adapter —
     命令回显"已切换", 实际这一轮对话还在用旧模型, 直到 /clear 才生效。
  2. `/provider X`、`/thinking`、`/config set api_key` 会 close() 掉
     state["_adapter"], 而运行中的 loop 正引用同一个对象 —
     下一回合直接 RuntimeError: client has been closed。

本模块的做法: 解析 (provider 作用域的 api_base/api_key) → 建新 adapter →
**先换入 loop 与子代理工具, 再关旧 adapter**。顺序不可颠倒。

另修一个隐蔽缺陷: api_base/api_key 此前是全局单值, 切到别的 provider 时
请求仍发往上一个 provider 的端点 (key 也一样)。这里按 provider 解析:
  - base: 配置里的 base 若明确属于**另一个已知 provider**, 跟随切换;
    若是自建网关 (不属于任何已知 provider), 保留 — 不夺走用户的代理设置。
  - key: provider env var → config [keys].<provider> → <provider>_api_key
    (旧字段) → 通用 [auth].api_key (最后兜底, 来源会如实告知)。

IPR-3: 只依赖 stdlib + 现有 config/adapter 层, 不引入模型 SDK。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

__all__ = [
    "Endpoint",
    "SwitchResult",
    "apply_switch",
    "build_provider_adapter",
    "provider_endpoint",
    "probe_models",
    "resolve_gateway",
]


# ── 智能网关目录 (2026-09-19 实测反馈: "只能选列表里的提供商") ──
# 常见 OpenAI 兼容网关: 名字即接入 (/provider zhipu), 免背 api_base —
# 此前用智谱/通义必须显式写 base, 打错就打到 api.openai.com。目录 +
# 模型探测把"三件套"压缩成"一个名字 + 一把 key"。
_KNOWN_GATEWAYS: dict[str, tuple[str, str, tuple[str, ...]]] = {
    # name: (api_base, display, aliases)
    "zhipu": ("https://open.bigmodel.cn/api/paas/v4", "Zhipu AI (GLM)",
              ("glm", "zhipuai", "bigmodel")),
    "qwen": ("https://dashscope.aliyuncs.com/compatible-mode/v1", "Alibaba Qwen",
             ("dashscope", "tongyi", "aliyun")),
    "kimi": ("https://api.moonshot.cn/v1", "Moonshot Kimi", ("moonshot",)),
    "grok": ("https://api.x.ai/v1", "xAI Grok", ("xai", "x-ai")),
    "openrouter": ("https://openrouter.ai/api/v1", "OpenRouter", ()),
    "siliconflow": ("https://api.siliconflow.cn/v1", "SiliconFlow (硅基流动)",
                    ("silicon",)),
    "groq": ("https://api.groq.com/openai/v1", "Groq", ()),
    "volcengine": ("https://ark.cn-beijing.volces.com/api/v3", "Volcengine Ark (豆包)",
                   ("doubao", "ark")),
    "minimax": ("https://api.minimax.chat/v1", "MiniMax", ()),
    "yi": ("https://api.lingyiwanwu.com/v1", "01.AI (Yi)", ("01ai", "lingyi")),
    "stepfun": ("https://api.stepfun.com/v1", "StepFun (阶跃星辰)", ("step",)),
    "together": ("https://api.together.xyz/v1", "Together AI", ()),
    "mistral": ("https://api.mistral.ai/v1", "Mistral AI", ()),
    "fireworks": ("https://api.fireworks.ai/inference/v1", "Fireworks AI", ()),
}

_GATEWAY_ALIASES: dict[str, str] = {
    alias: name
    for name, (_b, _d, aliases) in _KNOWN_GATEWAYS.items()
    for alias in aliases
}


def resolve_gateway(token: str) -> tuple[str, str, str] | None:
    """把用户输入解析成 (name, api_base, display)。

    接受: 目录名 (zhipu) / 别名 (glm, moonshot) / 完整 URL (https://api.x.ai/v1
    — host 命中目录时归一为目录名, 未命中时从 host 派生名字)。
    注册表里已有的 provider 名不在此处理 (走正常切换)。
    """
    t = (token or "").strip()
    if not t:
        return None
    low = t.lower()
    if low in _KNOWN_GATEWAYS:
        base, display, _a = _KNOWN_GATEWAYS[low]
        return low, base, display
    if low in _GATEWAY_ALIASES:
        name = _GATEWAY_ALIASES[low]
        base, display, _a = _KNOWN_GATEWAYS[name]
        return name, base, display
    # URL: https://host/path — host 命中目录 base → 归一; 否则派生名字
    if "://" in low or low.startswith("www."):
        candidate = t if "://" in t else f"https://{t}"
        try:
            host = (urlparse(candidate).hostname or "").lower()
        except Exception:
            return None
        if not host:
            return None
        for name, (base, display, _a) in _KNOWN_GATEWAYS.items():
            try:
                if (urlparse(base).hostname or "").lower() == host:
                    return name, base, display
            except Exception:
                continue
        slug = host
        for prefix in ("api.", "www.", "open.", "gateway.", "token.", "cdn.",
                       "app.", "dashboard."):
            if slug.startswith(prefix):
                slug = slug[len(prefix):]
                break
        slug = slug.split(".")[0].strip("-")
        if not slug:
            return None
        return slug, candidate.rstrip("/"), slug
    return None


def probe_models(api_base: str, api_key: str, timeout: float = 8.0,
                 transport: Any = None) -> list[str] | None:
    """探测 OpenAI 兼容网关的模型列表 (GET /models)。

    返回排序后的模型 id 列表; 端点不支持 / key 被拒 / 网络失败 → None
    (调用方据此提示, 不猜)。transport 参数供测试注入 httpx.MockTransport。
    """
    if not api_base:
        return None
    url = api_base.rstrip("/") + "/models"
    try:
        import httpx

        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        with httpx.Client(timeout=timeout, transport=transport) as client:
            resp = client.get(url, headers=headers)
        if resp.status_code != 200:
            return None
        data = resp.json().get("data", [])
        ids = sorted({str(m.get("id")) for m in data
                      if isinstance(m, dict) and m.get("id")})
        return ids or None
    except Exception:
        return None


@dataclass(frozen=True)
class Endpoint:
    """某 provider 生效的接入点 (base + key + 来源说明)。"""

    provider: str
    api_base: str = ""
    api_key: str = ""
    key_source: str = "none"
    base_source: str = "none"
    warn: str = ""


@dataclass
class SwitchResult:
    """切换结果 (供命令层渲染; 失败时 adapter 为 None)。"""

    ok: bool
    provider: str = ""
    model: str = ""
    display: str = ""
    endpoint: Endpoint | None = None
    adapter: Any = None
    error: str = ""
    live: bool = False            # 是否已换入正在运行的会话 (即立即生效)
    notes: list[str] = field(default_factory=list)
    persisted: bool = False


# ── host / provider 归属判定 ──


def _builtin_registry() -> set[str]:
    """内置 provider 名集合 (自定义 [[providers]] 之外的)。"""
    try:
        from zall._util.model_registry import _PROVIDER_REGISTRY
        return set(_PROVIDER_REGISTRY)
    except Exception:
        return set()


def _host_of(url: str) -> str:
    """从 URL 取 host (小写); 解析失败返回 ""。"""
    if not url:
        return ""
    try:
        return (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""


def _same_host(a: str, b: str) -> bool:
    """host 等价判定 (允许端口/子域差异的宽松匹配: a.example.com 与 example.com)。"""
    if not a or not b:
        return False
    return a == b or a.endswith("." + b) or b.endswith("." + a)


def _known_provider_of_host(host: str, registry: dict[str, Any]) -> str | None:
    """host 属于哪个已知 provider (registry 里 base 的 host 匹配)。

    只认 registry 中的 provider — 自建网关不在其中, 因此不会被误判。
    """
    if not host:
        return None
    for prov, meta in registry.items():
        if _same_host(host, _host_of(str(meta[2] or ""))):
            return prov
    return None


# ── 端点解析 ──


def provider_endpoint(
    provider: str, cfg: dict[str, Any] | None = None, *,
    assume_key: bool = False, inline_base: str = "",
) -> Endpoint:
    """解析 provider 作用域的 (api_base, api_key)。

    cfg 省略时读 load_config()。纯函数 (无 IO 副作用), 可离线单测。
    assume_key: 命令行已给定 key (inline) — base 归属判定时视同"有专属钥匙",
    跟随 provider 默认端点。
    inline_base: 命令行给定的端点 (最高优先级); 告警判定基于最终生效端点。
    """
    if cfg is None:
        try:
            from zall.safety.config import load_config
            cfg = load_config()
        except Exception:
            cfg = {}
    try:
        from zall.cli.config import _get_provider_registry
        registry = _get_provider_registry()
    except Exception:
        registry = {}
    meta = registry.get(provider)

    # ── api_base: 配置 base 属于别的 provider 时跟随切换; 自建网关保留 ──
    cfg_base = str(cfg.get("api_base") or "").strip()
    registry_base = str(meta[2] or "").strip() if meta else ""
    base_source = "config"
    api_base = cfg_base
    if not cfg_base:
        api_base, base_source = registry_base, "provider default"
    elif not registry_base:
        api_base, base_source = cfg_base, "config"
    elif _same_host(_host_of(cfg_base), _host_of(registry_base)):
        api_base, base_source = cfg_base, "config"
    else:
        owner = _known_provider_of_host(_host_of(cfg_base), registry)
        if owner and owner != provider:
            # 跟随默认端点的前提: 目标 provider 有自己的钥匙 (env/专属 key)。
            # 否则保留网关 — 用户可能正拿网关跑这家模型 (如 sensenova 网关
            # 跑 deepseek-chat), 夺走端点只会带来 401。
            _env_probe = str(meta[1] or "") if meta else ""
            # ollama 等本地 provider 不需要 key — 端点始终跟随默认
            _own_key = assume_key or provider == "ollama" or bool(
                (os.environ.get(_env_probe, "").strip() if _env_probe else "")
                or str((cfg.get("provider_keys") or {}).get(provider) or "").strip()
                or str(cfg.get(f"{provider}_api_key") or "").strip()
            )
            if _own_key:
                api_base = registry_base
                base_source = f"provider default (config base belongs to {owner})"
            else:
                api_base = cfg_base
                base_source = f"config ({owner} gateway — kept: no {provider}-specific key)"
        else:
            base_source = "config (custom gateway)"
    if inline_base:
        api_base, base_source = inline_base, "inline (this session)"

    # ── api_key: provider env → config [keys] → <provider>_api_key → 通用 key ──
    env_name = str(meta[1] or "") if meta else ""
    env_val = os.environ.get(env_name, "").strip() if env_name else ""
    provider_keys = cfg.get("provider_keys") or {}
    per_key = ""
    if isinstance(provider_keys, dict):
        per_key = str(provider_keys.get(provider) or "").strip()
    legacy_key = str(cfg.get(f"{provider}_api_key") or "").strip()
    generic = str(cfg.get("api_key") or "").strip()
    _PLACEHOLDER = "your-api-key-here"

    api_key, key_source = "", "none"
    if env_val and env_val != _PLACEHOLDER:
        api_key, key_source = env_val, f"env {env_name}"
    elif per_key and per_key != _PLACEHOLDER:
        api_key, key_source = per_key, f"config [keys].{provider}"
    elif legacy_key and legacy_key != _PLACEHOLDER:
        api_key, key_source = legacy_key, f"config [{provider}_api_key]"
    elif generic and generic != _PLACEHOLDER:
        api_key, key_source = generic, "config [auth].api_key (generic)"

    # 通用 key 只有在端点也还是原 provider 时才可信; 跨 provider 时如实告警
    warn = ""
    if key_source.endswith("(generic)") and not _same_host(
        _host_of(cfg_base), _host_of(api_base)
    ):
        warn = f"no {provider}-specific key found; the generic key may not work here"

    return Endpoint(
        provider=provider, api_base=api_base, api_key=api_key,
        key_source=key_source, base_source=base_source, warn=warn,
    )


def _adapter_ctor_params(
    provider: str, _fallback: Any | None = None
) -> set[str]:
    """目标 adapter 构造器接受的参数名 (未知 provider 用 OpenAICompat 形状)。

    provider: provider 名 (用于查 registry)
    _fallback: 已知要用的 adapter 类; 给出时跳过 registry 查找 (供 _build_adapter
               复用, 避免重复 importlib)。
    """
    import inspect

    cls: Any = _fallback
    if cls is None:
        try:
            from zall.cli.config import _get_provider_registry
            meta = _get_provider_registry().get(provider)
        except Exception:
            meta = None
        try:
            if meta is not None:
                import importlib
                mod_path, cls_name = str(meta[5]).split(":")
                cls = getattr(importlib.import_module(mod_path), cls_name)
            else:
                from zall.adapters import OpenAICompatAdapter as cls
        except Exception:
            return {"api_key", "api_base", "model", "timeout"}
    try:
        params = inspect.signature(cls.__init__).parameters
    except (TypeError, ValueError):
        return {"api_key", "api_base", "model", "timeout"}
    # **kwargs 形状的构造器什么都收, 不做白名单过滤 (否则全被滤掉)
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return set(params) - {"self"}
    return set(params) - {"self"}


def build_provider_adapter(
    provider: str,
    model: str,
    *,
    cfg: dict[str, Any] | None = None,
    timeout: float | None = None,
    api_key: str | None = None,
    api_base: str | None = None,
) -> tuple[Any, Endpoint]:
    """按 provider 作用域建 adapter (返回 (adapter, endpoint))。

    api_key/api_base 显式传入时优先 (命令行的 key=/base= 场景)。
    只把目标构造器真正接受的字段传下去 — anthropic/gemini 不收 api_base、
    ollama 用的是 host, 一律按签名过滤, 避免 TypeError。
    """
    from zall.cli import config as _cli_config

    ep = provider_endpoint(provider, cfg=cfg, assume_key=bool(api_key),
                           inline_base=api_base or "")
    if api_key:
        # inline key 是用户显式给的 — "generic key 可能不工作"的告警不再适用
        ep = Endpoint(**{**ep.__dict__, "api_key": api_key,
                         "key_source": "inline (this session)", "warn": ""})
    params = _adapter_ctor_params(provider)
    kwargs: dict[str, Any] = {}
    if ep.api_key and "api_key" in params:
        kwargs["api_key"] = ep.api_key
    if ep.api_base and "api_base" in params:
        kwargs["api_base"] = ep.api_base
    if ep.api_base and "host" in params:  # ollama
        kwargs["host"] = ep.api_base
    adapter = _cli_config._build_adapter(provider, model=model, timeout=timeout, **kwargs)
    return adapter, ep


# ── 热切换 ──


def _sync_live_loop(loop: Any, adapter: Any) -> Any:
    """把新 adapter 换进正在运行的 loop (含持有引用的子代理工具)。

    返回旧 adapter (无 loop 时 None)。子代理工具必须同步, 否则它继续用旧
    adapter — 而旧 adapter 马上会被关闭。
    """
    if loop is None:
        return None
    if hasattr(loop, "set_model_adapter"):
        previous = loop.set_model_adapter(adapter)
    else:  # 兼容测试替身/第三方 loop: 走私有字段
        previous = getattr(loop, "_model", None)
        loop._model = adapter
    tools = getattr(loop, "_tools", None)
    spawn = None
    if tools is not None and hasattr(tools, "get"):
        try:
            spawn = tools.get("spawn_subagent")
        except Exception:
            spawn = None
    if spawn is not None and hasattr(spawn, "swap_model_provider"):
        spawn.swap_model_provider(adapter)
    return previous


def _close_quietly(adapter: Any) -> None:
    if adapter is None:
        return
    close = getattr(adapter, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def _reset_session_stats(state: dict[str, Any] | None) -> None:
    """切换后清零缓存统计 (缓存按 provider/模型分片, 旧的命中率不再描述现状)。"""
    if not state:
        return
    stats = state.get("cache_stats")
    if stats is not None and hasattr(stats, "reset"):
        try:
            stats.reset()
        except Exception:
            pass


def apply_switch(
    state: dict[str, Any] | None,
    loop: Any | None,
    *,
    model: str | None = None,
    provider: str | None = None,
    persist: bool = False,
    timeout: float | None = None,
    inline_key: str | None = None,
    inline_base: str | None = None,
    persist_key: bool = False,
) -> SwitchResult:
    """切换当前会话的 provider/model — **立即生效**, 无需 /clear。

    state: REPL/TUI 共享状态 (更新 model/_adapter/cache_stats)
    loop:  正在运行的 AgentLoop (None = 还没开始对话, 只改 state)
    model: 目标模型 (省略则用 provider 默认模型)
    provider: 目标 provider (省略则按 model 名推断)
    persist: 写入 ~/.zall/config.toml (model + 需要时的 api_base)
    inline_key/inline_base: 本会话使用的 key/base (persist_key=True 时落盘到 [keys])
    """
    from zall.cli.config import (
        _detect_provider,
        _get_provider_registry,
        _persist_model_to_config,
        _resolve_model_alias,
    )
    from zall._util.model_registry import get_provider_default_model, get_provider_display

    state = state if state is not None else {}
    cfg: dict[str, Any] = {}
    try:
        from zall.safety.config import load_config
        cfg = load_config()
    except Exception:
        cfg = {}

    target_model = _resolve_model_alias(model) if model else ""
    target_provider = (provider or "").strip()
    registry = _get_provider_registry()
    # 自定义 provider 名可能大小写敏感: 先试原名, 再试小写
    if target_provider and target_provider not in registry \
            and target_provider.lower() in registry:
        target_provider = target_provider.lower()

    if target_provider:
        if target_provider not in registry:
            # OpenCode 对齐: 任意来源三件套 (base + key + model) 直接接入,
            # 不必预注册。判定从紧: 通用 [auth].api_key / 通用 api_base 是
            # "当前 provider"的配置, 对新网关大概率 401 — base 必须 inline 给,
            # key 须 inline 或该 provider 专属 ([keys].<p> / <p>_api_key)。
            _ep_probe = provider_endpoint(target_provider, cfg=cfg)
            _ks = _ep_probe.key_source
            _key_ok = bool(inline_key) or _ks.startswith("env ") \
                or ".keys." in _ks or _ks.endswith("_api_key]")
            if not (inline_base and _key_ok):
                valid = ", ".join(registry.keys())
                return SwitchResult(
                    ok=False, provider=target_provider,
                    error=(
                        f"unknown provider '{target_provider}' and no usable endpoint. "
                        f"three fields are enough: "
                        f"/provider {target_provider} base=<url> key=<key> [model=<id>]  "
                        f"(known: {valid})"
                    ),
                )
        if not target_model:
            target_model = get_provider_default_model(target_provider, registry=registry)
            if not target_model:
                # 自定义/未注册 provider 无预设模型 → 沿用当前模型
                target_model = (state.get("model") or str(cfg.get("model") or "")).strip()
                if not target_model:
                    return SwitchResult(
                        ok=False, provider=target_provider,
                        error=f"provider '{target_provider}' has no preset model; "
                              f"add model=<id> (or /model <name> first)",
                    )
    else:
        if not target_model:
            return SwitchResult(ok=False, error="no model given")
        target_provider = _detect_provider(target_model)

    try:
        adapter, endpoint = build_provider_adapter(
            target_provider, target_model, cfg=cfg, timeout=timeout,
            api_key=inline_key, api_base=inline_base,
        )
    except ValueError as e:
        return SwitchResult(ok=False, provider=target_provider, model=target_model, error=str(e))
    except Exception as e:  # 建不起来就保持现状, 明确报出
        return SwitchResult(ok=False, provider=target_provider, model=target_model,
                            error=f"{type(e).__name__}: {e}")

    # 先换入 (loop + 子代理工具 + state), 再关旧 adapter
    previous = _sync_live_loop(loop, adapter)
    old_state_adapter = state.get("_adapter")
    state["_adapter"] = adapter
    state["model"] = target_model
    state["provider"] = target_provider
    _reset_session_stats(state)
    if previous is None and old_state_adapter is not adapter:
        previous = old_state_adapter
    if previous is not None and previous is not adapter:
        _close_quietly(previous)

    result = SwitchResult(
        ok=True, provider=target_provider, model=target_model,
        display=get_provider_display(target_provider),
        endpoint=endpoint, adapter=adapter, live=loop is not None,
    )
    if endpoint.warn:
        result.notes.append(endpoint.warn)
    if endpoint.key_source == "none" and target_provider != "ollama":
        result.notes.append(
            f"no API key for {target_provider} — run /provider "
            f"{target_provider} to enter one (hidden prompt), or set "
            f"{endpoint_env_hint(target_provider)}"
        )

    if persist:
        try:
            _persist_model_to_config(
                target_model, api_base=endpoint.api_base or None,
                provider=target_provider)
            result.persisted = True
        except Exception as e:
            result.notes.append(f"persist failed: {e}")
    if persist and persist_key and inline_key:
        try:
            from zall.cli.provider_keys import persist_provider_key
            persist_provider_key(target_provider, inline_key)
        except Exception as e:
            result.notes.append(f"key persist failed: {e}")
    if persist and target_provider not in _builtin_registry() and endpoint.api_base:
        # 三件套接入的自定义 provider: 落盘 [[providers]] 条目,
        # 下次 /provider <name> 一键切回 (不必再给 base/key)
        try:
            from zall.cli.provider_keys import persist_custom_provider
            persist_custom_provider(target_provider, endpoint.api_base)
        except Exception as e:
            result.notes.append(f"provider persist failed: {e}")

    return result


def endpoint_env_hint(provider: str) -> str:
    """该 provider 的 env var 名 (供提示文案; 无则给通用名)。"""
    try:
        from zall.cli.config import _get_provider_registry
        meta = _get_provider_registry().get(provider)
        if meta is not None and meta[1]:
            return str(meta[1])
    except Exception:
        pass
    return "ZALL_API_KEY"