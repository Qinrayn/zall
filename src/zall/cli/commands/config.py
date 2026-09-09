"""zall.cli.commands.config - /config command (F1: 配置一等化).

对应: 配置缺口审计 -- api_key/api_base/timeout/window_size/采样参数此前无命令设置。
设计: 不碰 model 名 (/model) 和 provider 切换 (/provider), 只填缺口。
不重复 /doctor (只读诊断) 和 /thinking (Anthropic budget)。

用法:
  /config                         展示当前配置 (api_key 脱敏)
  /config set <key> <value>       设置 + 持久化到 ~/.zall/config.toml
  /config unset <key>             清除某项
  /config guide                   配置指引

可设 key:
  api_key, api_base, timeout, window_size,
  temperature, max_tokens, top_p, reasoning_effort
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from zall.cli.commands._common import _CATEGORY_MODEL, slash_command

_CATEGORY_CONFIG = _CATEGORY_MODEL  # 归入 model 配置类

# 可经 /config set 设置的 key -> (toml_section, value_type, env_var)
# value_type: "str" | "float" | "int" | "effort"
_CONFIG_KEYS: dict[str, tuple[str, str, str]] = {
    "api_key":          ("auth",  "str",    "ZALL_API_KEY"),
    "api_base":         ("model", "str",    "ZALL_API_BASE"),
    "provider":         ("model", "str",    "ZALL_PROVIDER"),
    "timeout":          ("model", "float",  "ZALL_TIMEOUT"),
    "window_size":      ("model", "int",    "ZALL_WINDOW_SIZE"),
    "temperature":      ("model", "float",  "ZALL_TEMPERATURE"),
    "max_tokens":       ("model", "int",    "ZALL_MAX_TOKENS"),
    "top_p":            ("model", "float",  "ZALL_TOP_P"),
    "reasoning_effort": ("model", "effort", "ZALL_REASONING_EFFORT"),
    "theme":            ("ui",    "theme",  "ZALL_THEME"),
}


def _mask_key(key: str) -> str:
    """脱敏 API key: 只显后 4 位。"""
    if not key or key == "your-api-key-here":
        return "(not set)"
    if len(key) <= 8:
        return "****"
    return key[:3] + "..." + key[-4:]


def _parse_value(key: str, raw: str) -> Any:
    """把用户输入的字符串转为对应类型的值。失败抛 ValueError。"""
    vtype = _CONFIG_KEYS[key][1]
    if vtype == "str":
        return raw
    if vtype == "float":
        return float(raw)
    if vtype == "int":
        return int(raw)
    if vtype == "effort":
        val = raw.strip().lower()
        if val not in ("low", "medium", "high"):
            raise ValueError(f"reasoning_effort must be low/medium/high, got '{raw}'")
        return val
    if vtype == "theme":
        from zall.cli import theme as theme_mod
        val = raw.strip().lower()
        if val not in theme_mod.THEMES:
            raise ValueError(
                f"theme must be one of {'/'.join(theme_mod.list_themes())}, got '{raw}'"
            )
        return val
    return raw


def _render_value(key: str, value: Any) -> str:
    """渲染配置值用于展示。"""
    if key == "api_key":
        return _mask_key(str(value)) if value else "(not set)"
    if value is None:
        return "(unset)"
    return str(value)


def _persist_config_key(key: str, value: Any | None, config_path: Path | None = None) -> Path:
    """持久化单个 config key 到 ~/.zall/config.toml (保留其他段/键)。

    value=None 表示删除该 key。
    config_path: 测试注入用; 默认从当前 Path.home() 解析 (非模块级 CONFIG_DIR,
    以便 monkeypatch HOME 能重定向)。
    """
    from zall._util.toml import load_toml_simple as _load_toml_simple
    section, _vtype, _env = _CONFIG_KEYS[key]
    if config_path is None:
        config_path = Path.home() / ".zall" / "config.toml"
    try:
        config_path.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    # 读取现有内容 (保留结构)
    data: dict[str, Any] = {}
    existing_lines: list[str] = []
    if config_path.exists():
        try:
            existing_lines = config_path.read_text(encoding="utf-8").splitlines(keepends=True)
            data = _load_toml_simple(config_path)
        except Exception:
            existing_lines = []
            data = {}

    def _emit_kv(k: str, v: Any) -> str:
        if isinstance(v, str):
            return f'{k} = "{v}"\n'
        return f'{k} = {v}\n'

    # 解析现有段落 (复用 _persist_model_to_config 的段落保留模式)
    from zall._util.toml import extract_section_name as _extract_section_name
    sections: list[tuple[str, list[str]]] = []
    current_lines: list[str] = []
    current_section = ""
    for line in existing_lines:
        stripped = line.strip()
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

    # 更新目标段的 data 视图
    sec_data: dict[str, Any] = dict(data.get(section, {}))
    if value is None:
        sec_data.pop(key, None)
    else:
        sec_data[key] = value
    # 特殊: auth 段的 api_key
    if section == "auth":
        sec_data = {"api_key": value if value is not None else sec_data.get("api_key", "")}

    new_lines: list[str] = []
    has_target = False
    for name, lines in sections:
        _top = name.split(".")[0].strip() if "." in name else name.strip()
        if _top == section:
            if has_target:
                continue  # 去重
            new_lines.append(f"[{section}]\n")
            for k, v in sec_data.items():
                new_lines.append(_emit_kv(k, v))
            has_target = True
        else:
            new_lines.extend(lines)
    if not has_target:
        new_lines.append(f"[{section}]\n")
        for k, v in sec_data.items():
            new_lines.append(_emit_kv(k, v))

    config_path.write_text("".join(new_lines), encoding="utf-8")
    return config_path


def _show_config(out: Any) -> str:
    """展示当前全部配置 (api_key 脱敏)。"""
    from zall.safety.config import load_config
    try:
        cfg = load_config()
    except Exception as e:
        out.write(f"  config load error: {e}\n")
        return "handled"

    out.write("  Current configuration:\n")
    out.write(f"    model            : {cfg.get('model') or '(not set)'}\n")
    out.write(f"    api_key          : {_mask_key(str(cfg.get('api_key') or ''))}\n")
    out.write(f"    api_base         : {cfg.get('api_base') or '(not set)'}\n")
    out.write(f"    timeout          : {cfg.get('timeout')}s\n")
    out.write(f"    window_size      : {_render_value('window_size', cfg.get('window_size'))}\n")
    out.write("  Sampling parameters:\n")
    out.write(f"    temperature      : {_render_value('temperature', cfg.get('temperature'))}\n")
    out.write(f"    max_tokens       : {_render_value('max_tokens', cfg.get('max_tokens'))}\n")
    out.write(f"    top_p            : {_render_value('top_p', cfg.get('top_p'))}\n")
    out.write(f"    reasoning_effort : {_render_value('reasoning_effort', cfg.get('reasoning_effort'))}\n")
    out.write("\n  /config set <key> <value>   /config unset <key>   /config guide\n")
    return "handled"


def _show_guide(out: Any) -> str:
    """配置指引 — 先讲“任意来源只需 3 个字段”的通用接入。"""
    out.write("  Connect ANY model source with 3 fields — api_key + base_url + model id:\n")
    out.write("    /config set api_base https://your-endpoint.com/v1\n")
    out.write("    /config set api_key  sk-...\n")
    out.write("    /model <model-id>\n")
    out.write("\n  Or edit ~/.zall/config.toml — one [model] block does it all:\n")
    out.write("    [model]\n")
    out.write('    name = "your-model-id"\n')
    out.write('    api_base = "https://your-endpoint.com/v1"\n')
    out.write('    api_key = "sk-..."       # optional here; or [auth].api_key / ZALL_API_KEY\n')
    out.write('    provider = "openai"      # optional; force adapter openai|anthropic|gemini|ollama\n')
    out.write("\n  Other settable keys (persist to ~/.zall/config.toml):\n")
    out.write("    /config set timeout 300\n")
    out.write("    /config set window_size 64000      # override context window for current model\n")
    out.write("    /config set temperature 0.3        # sampling temperature\n")
    out.write("    /config set max_tokens 4096        # max output tokens\n")
    out.write("    /config set top_p 0.9              # nucleus sampling\n")
    out.write("    /config set reasoning_effort high  # low/medium/high (supported models only)\n")
    out.write("\n  Or via environment variables (override config):\n")
    out.write("    ZALL_API_KEY, ZALL_API_BASE, ZALL_MODEL, ZALL_PROVIDER, ZALL_TIMEOUT,\n")
    out.write("    ZALL_WINDOW_SIZE, ZALL_TEMPERATURE, ZALL_MAX_TOKENS,\n")
    out.write("    ZALL_TOP_P, ZALL_REASONING_EFFORT\n")
    out.write("\n  Notes:\n")
    out.write("    - model id: use /model <name> (or ZALL_MODEL / [model].name)\n")
    out.write("    - provider: any OpenAI-compatible endpoint works with the default (openai);\n")
    out.write("      set provider only to force a non-OpenAI adapter (anthropic/gemini/ollama).\n")
    out.write("    - reasoning_effort: only some models support it (e.g. o-series);\n")
    out.write("      unsupported providers may return HTTP 400.\n")
    return "handled"


def _rebuild_adapter(state: dict[str, Any] | None) -> None:
    """设置采样参数后重建 adapter, 使下次对话生效 (复用 /thinking 模式)。"""
    if state is None:
        return
    _ad = state.pop("_adapter", None)
    if _ad is not None and hasattr(_ad, "close"):
        try:
            _ad.close()
        except Exception:
            pass


@slash_command("/config", description="show/set configuration (api_key, api_base, window_size, sampling params)", category=_CATEGORY_CONFIG)
def cmd_config(arg: str, out: Any, loop: Any | None = None, state: dict[str, Any] | None = None) -> str:
    """Show or set zall configuration (F1).

    Does NOT handle model name (/model) or provider switching (/provider) -
    those have their own commands. This fills the gap: api_key, api_base,
    timeout, window_size, and sampling parameters.
    """
    parts = (arg or "").split()
    if not parts:
        return _show_config(out)

    subcmd = parts[0].lower()
    if subcmd == "guide":
        return _show_guide(out)
    if subcmd in ("show", "list", "status"):
        return _show_config(out)

    if subcmd == "set":
        if len(parts) < 3:
            out.write("  usage: /config set <key> <value>\n")
            out.write(f"  keys: {', '.join(_CONFIG_KEYS.keys())}\n")
            return "handled"
        key = parts[1].lower()
        if key not in _CONFIG_KEYS:
            out.write(f"  unknown key '{key}'. valid: {', '.join(_CONFIG_KEYS.keys())}\n")
            return "handled"
        raw_value = " ".join(parts[2:])
        try:
            value = _parse_value(key, raw_value)
        except ValueError as e:
            out.write(f"  invalid value: {e}\n")
            return "handled"
        try:
            path = _persist_config_key(key, value)
            # 同步设 env (使本会话立即生效, 无需重启)
            env_var = _CONFIG_KEYS[key][2]
            if value is not None:
                os.environ[env_var] = str(value)
            else:
                os.environ.pop(env_var, None)
            # 重建 adapter 使采样参数生效 (api_key/base/timeout 也需新 adapter)
            _rebuild_adapter(state)
            if key == "theme":  # G6: REPL 即时换肤 (TUI 重启生效)
                from zall.cli import theme as theme_mod
                theme_mod.switch(str(value))
            out.write(f"  \u2713 {key} set to {value if key != 'api_key' else '****'}\n")
            out.write(f"    persisted to {path}\n")
            if key in ("temperature", "max_tokens", "top_p", "reasoning_effort"):
                out.write("    (takes effect next turn; adapter rebuilt)\n")
        except Exception as e:
            out.write(f"  failed to persist: {e}\n")
        return "handled"

    if subcmd == "unset":
        if len(parts) < 2:
            out.write("  usage: /config unset <key>\n")
            return "handled"
        key = parts[1].lower()
        if key not in _CONFIG_KEYS:
            out.write(f"  unknown key '{key}'. valid: {', '.join(_CONFIG_KEYS.keys())}\n")
            return "handled"
        try:
            path = _persist_config_key(key, None)
            env_var = _CONFIG_KEYS[key][2]
            os.environ.pop(env_var, None)
            _rebuild_adapter(state)
            out.write(f"  \u2713 {key} unset\n")
            out.write(f"    persisted to {path}\n")
        except Exception as e:
            out.write(f"  failed to unset: {e}\n")
        return "handled"

    out.write("  usage: /config | /config set <key> <value> | /config unset <key> | /config guide\n")
    return "handled"


@slash_command("/theme", description="show color theme (attic)", category=_CATEGORY_CONFIG)
def cmd_theme(arg: str, out: Any, loop: Any | None = None, state: dict[str, Any] | None = None) -> str:
    """G6 主题切换: /theme 列出, /theme <name> 切换+持久化。

    REPL 色板即时生效 (theme.switch 写 render._C/_ANSI_MAP/CODE_*);
    TUI 色板在启动时构建, 需重启生效。
    """
    from zall.cli import theme as theme_mod
    name = (arg or "").strip().lower()
    if not name or name in ("show", "list"):
        current = theme_mod.active_name()
        out.write("  themes:\n")
        for t in theme_mod.list_themes():
            marker = "*" if t == current else " "
            out.write(f"   {marker} {t}\n")
        out.write("  usage: /theme <name>   (persists to ~/.zall/config.toml [ui].theme)\n")
        return "handled"
    try:
        theme_mod.switch(name)
    except ValueError as e:
        out.write(f"  {e}\n")
        return "handled"
    try:
        path = _persist_config_key("theme", name)
        os.environ["ZALL_THEME"] = name
        out.write(f"  \u2713 theme \u2192 {name} (REPL immediate; TUI on restart)\n")
        out.write(f"    persisted to {path}\n")
    except Exception as e:
        out.write(f"  theme applied for this session; persist failed: {e}\n")
    return "handled"
