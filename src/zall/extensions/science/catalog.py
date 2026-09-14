"""zall.extensions.science.catalog — 数据驱动的科研模块目录。

Argus modules.json 模式对标 (原创实现): 每个科研模块 = 一条可验证主张 +
一个执行方式, 全部声明在 JSON 目录里, 代码只负责加载/查找/执行:

  - certifier 模式: in-process 调用 core.proof_gate 认证器 (交互, 秒级)
  - script 模式:    subprocess 跑 experiments/ 驱动脚本 (重量级, 出 artifact 报告)

目录来源优先级 (后者 overlay 前者, 按 id 合并):
  1. 包内 catalog.json (内置模块)
  2. ~/.zall/science_catalog.json (用户扩展)
  3. $ZALL_SCIENCE_CATALOG 指定的文件 (最高优先, 供测试/定制)

id 规范化: 目录内 id 冲突/缺失时自动重排 (Argus normalize_catalog_ids 思想) —
坏目录不崩溃, 降级可用。
"""

from __future__ import annotations

import difflib
import json
import re
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_PACKAGE_CATALOG = Path(__file__).parent / "catalog.json"
_USER_CATALOG = Path.home() / ".zall" / "science_catalog.json"
_ENV_KEY = "ZALL_SCIENCE_CATALOG"


@dataclass(frozen=True)
class SciModule:
    """科研模块目录条目 (自描述: 展示/执行/校验所需的一切)。"""

    id: str
    name: str
    description: str = ""
    section: str = "Research"
    primary_input: str = ""
    options: tuple[str, ...] = ()
    options_help: dict[str, str] = field(default_factory=dict)
    certifier: str = ""                     # in-process: proof_gate 函数名
    script: str = ""                        # subprocess: 仓库相对脚本路径
    script_args: tuple[str, ...] = ()       # 支持 {option} / {out_dir} 占位
    report: str = ""                        # script 模式: 报告 JSON 相对路径 (相对脚本 cwd)
    report_tier_path: str = ""              # script 模式: 报告 JSON 内 tier 的 dot-path (bool: true→proven)
    tags: frozenset[str] = frozenset()

    @property
    def is_in_process(self) -> bool:
        return bool(self.certifier)


def _module_from_dict(d: dict[str, Any], fallback_id: str) -> SciModule:
    mid = str(d.get("id", "") or "").strip() or fallback_id
    return SciModule(
        id=mid,
        name=str(d.get("name", f"Module {mid}")),
        description=str(d.get("description", "")),
        section=str(d.get("section", "Research")),
        primary_input=str(d.get("primary_input", "")),
        options=tuple(str(o) for o in (d.get("options") or [])),
        options_help={str(k): str(v) for k, v in (d.get("options_help") or {}).items()},
        certifier=str(d.get("certifier", "")),
        script=str(d.get("script", "")),
        script_args=tuple(str(a) for a in (d.get("script_args") or [])),
        report=str(d.get("report", "")),
        report_tier_path=str(d.get("report_tier_path", "")),
        tags=frozenset(str(t) for t in (d.get("tags") or [])),
    )


def _load_json_modules(path: Path) -> list[dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    mods = data.get("modules") if isinstance(data, dict) else data
    return [m for m in (mods or []) if isinstance(m, dict)]


def _normalize_ids(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """id 冲突/缺失自动重排 (坏目录降级可用, 不崩)。"""
    seen: set[str] = set()
    for m in raw:
        mid = str(m.get("id", "") or "").strip()
        if not mid or mid in seen:
            mid = str(max((int(x) for x in seen if x.isdigit()), default=0) + 1)
            m["id"] = mid
        seen.add(mid)
    return raw


def catalog_paths() -> list[Path]:
    """目录来源 (低→高优先级): 包内置 → 用户 overlay → env 指定。"""
    paths = [_PACKAGE_CATALOG, _USER_CATALOG]
    env = os.environ.get(_ENV_KEY, "").strip()
    if env:
        paths.append(Path(env))
    return paths


def load_catalog() -> list[SciModule]:
    """加载合并后的模块目录 (按 id overlay: 高优先级来源覆盖同名条目)。"""
    by_id: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for path in catalog_paths():
        for raw in _normalize_ids(_load_json_modules(path)):
            mid = str(raw.get("id"))
            if mid not in by_id:
                order.append(mid)
            by_id[mid] = raw
    return [_module_from_dict(by_id[mid], mid) for mid in order]


def find_modules(query: str, catalog: list[SciModule] | None = None) -> list[SciModule]:
    """Argus fuzzy_find_modules 对标: 精确 id → 精确名 → 子串 → difflib 模糊。"""
    mods = catalog if catalog is not None else load_catalog()
    q = query.strip()
    if not q:
        return list(mods)
    exact = [m for m in mods if q == m.id or q.lower() == m.name.lower()]
    if exact:
        return exact
    part = [m for m in mods
            if q.lower() in m.name.lower() or q.lower() in m.description.lower()
            or any(q.lower() in t for t in m.tags)]
    if part:
        return part
    close = difflib.get_close_matches(q, [m.name for m in mods], n=5, cutoff=0.5)
    hits = [m for m in mods if m.name in close]
    if hits:
        return hits
    # token 级模糊兜底: 拼写错误通常命中名字里的单词 (coverng → Covering)
    ql = q.lower()
    token_hits = []
    for m in mods:
        words = [w for w in re.split(r"[\s\-/]+", m.name.lower()) if w]
        if difflib.get_close_matches(ql, words, n=1, cutoff=0.75):
            token_hits.append(m)
    return token_hits
