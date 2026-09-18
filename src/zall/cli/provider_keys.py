"""zall.cli.provider_keys — 每供应商 API key 的落盘 (config.toml `[keys]` 段)。

设计: 一家供应商一个 key, 与 [auth].api_key (默认/兜底) 分开存。

    [keys]
    deepseek = "sk-..."
    openai   = "sk-..."

这样 `/provider deepseek key=sk-...` 之后, 再 `/provider openai` 时不会互相覆盖,
也不需要用户手工编辑 config.toml。读取侧见 zall.safety.config.load_config
(provider_keys) 与 zall.cli.model_switch.provider_endpoint。
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["persist_custom_provider", "persist_provider_key", "remove_provider_key"]


def _config_path(config_path: Path | None = None) -> Path:
    if config_path is not None:
        return config_path
    # 与 safety.config.CONFIG_DIR 单源对齐 (测试 monkeypatch CONFIG_DIR 即隔离,
    # 不再旁路 resolve_home_dir 写进真实 HOME)
    from zall.safety.config import CONFIG_DIR
    return Path(CONFIG_DIR) / "config.toml"


def _render_key(line_key: str, value: str) -> str:
    return f'{line_key} = "{value}"\n'


def _update_keys_section(lines: list[str], provider: str, key: str | None) -> list[str]:
    """在原始行列表上更新 [keys] 段 (保留其他段与注释)。

    段内同名的旧行丢弃并以新值重建, 段内顺序保持 (新 key 追加到段尾)。
    """
    out: list[str] = []
    in_keys = False
    seen_section = False
    wrote = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("["):
            if in_keys and not wrote:
                # 离开 [keys] 段前补写新键 (若该段内没有该 provider)
                if key is not None:
                    out.append(_render_key(provider, key))
                wrote = True
            in_keys = stripped.startswith("[keys]") or stripped.startswith("[keys.")
            if in_keys:
                seen_section = True
            out.append(line)
            continue
        if in_keys:
            k = stripped.split("=", 1)[0].strip().strip('"') if "=" in stripped else ""
            if k == provider:
                if key is not None and not wrote:
                    out.append(_render_key(provider, key))
                wrote = True
                continue  # 丢弃旧行 (更新或删除)
        out.append(line)
    if in_keys and not wrote and key is not None:
        out.append(_render_key(provider, key))
        wrote = True
    if not seen_section and key is not None:
        if out and not out[-1].endswith("\n"):
            out.append("\n")
        if out and out[-1].strip():
            out.append("\n")
        out.append("[keys]\n")
        out.append(_render_key(provider, key))
    return out


def persist_provider_key(provider: str, key: str, config_path: Path | None = None) -> Path:
    """写入/更新 [keys].<provider>; 文件不存在则创建。"""
    path = _config_path(config_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    if path.exists():
        try:
            lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        except (OSError, UnicodeDecodeError):
            lines = []
    path.write_text("".join(_update_keys_section(lines, provider, key)), encoding="utf-8")
    return path


def remove_provider_key(provider: str, config_path: Path | None = None) -> Path:
    """删除 [keys].<provider> (其余不动)。"""
    path = _config_path(config_path)
    if not path.exists():
        return path
    try:
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    except (OSError, UnicodeDecodeError):
        return path
    path.write_text("".join(_update_keys_section(lines, provider, None)), encoding="utf-8")
    return path


def persist_custom_provider(
    name: str, api_base: str, config_path: Path | None = None
) -> Path:
    """写入/更新 [[providers]] 条目 (base + key 落盘后, /provider <name> 一键切回)。

    同名条目存在则只更新其 api_base (其余字段原样保留); 不存在则在文件尾
    追加新条目。key 走 [keys] 段 (persist_provider_key), 两者配合就是
    "三件套接入"的持久化。其他条目/段落一行不动。
    """
    path = _config_path(config_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    if path.exists():
        try:
            lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        except (OSError, UnicodeDecodeError):
            lines = []

    def _entry_block() -> list[str]:
        return [
            "[[providers]]\n",
            f'name = "{name}"\n',
            f'display = "{name}"\n',
            'adapter = "openai-compat"\n',
            f'api_base = "{api_base}"\n',
        ]

    # 先切块: 每个 [[providers]] 条目的行区间 (到下一个段头行之前)
    blocks: list[tuple[int, int]] = []  # (start, end) 半开区间, end 含段尾
    i = 0
    while i < len(lines):
        if lines[i].strip().startswith("[[providers]]"):
            j = i + 1
            while j < len(lines) and not lines[j].strip().startswith("["):
                j += 1
            blocks.append((i, j))
            i = j
        else:
            i += 1

    def _block_name(start: int, end: int) -> str:
        for line in lines[start:end]:
            s = line.strip()
            if s.startswith("name"):
                return s.split("=", 1)[1].strip().strip('"').strip("'")
        return ""

    updated = False
    out: list[str] = []
    prev_end = 0
    for start, end in blocks:
        if _block_name(start, end) == name and not updated:
            # 同名条目: 原样保留其他行, 替换其中的 api_base 行
            out.extend(lines[prev_end:start])
            for line in lines[start:end]:
                if line.strip().startswith("api_base"):
                    out.append(f'api_base = "{api_base}"\n')
                else:
                    out.append(line)
            updated = True
            prev_end = end
    if updated:
        out.extend(lines[prev_end:])
    else:
        out.extend(lines)
        # 文件尾追加: 确保前面有换行分隔
        while out and out[-1].strip() == "":
            out.pop()
        if out:
            out.append("\n")
        out.extend(_entry_block())
    path.write_text("".join(out), encoding="utf-8")
    return path