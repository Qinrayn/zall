"""zall.extensions.science.profiles — 研究深度预设 (Argus profile 对标)。

Argus 用 speed/deep/safe 预设批量设全局选项; zall 的科研版按"验证深度"分档:
预设只提供**默认值** — 用户已显式 set 的选项优先 (不覆盖手动配置)。
"""

from __future__ import annotations

# 选项名用下划线规范形 (与 state 存储一致); 目录里的连字符选项加载时归一。
PROFILES: dict[str, dict[str, str]] = {
    "quick": {
        "residual_bound": "3000",
        "coef_max": "4",
        "search_bound": "32",
        "timeout": "120",
    },
    "deep": {
        "residual_bound": "20000",
        "coef_max": "6",
        "search_bound": "64",
        "timeout": "900",
    },
    "exhaustive": {
        "residual_bound": "100000",
        "coef_max": "8",
        "search_bound": "256",
        "timeout": "3600",
    },
}

DEFAULT_PROFILE = "deep"


def profile_names() -> list[str]:
    return list(PROFILES)


def apply_profile(name: str, options: dict[str, str]) -> dict[str, str]:
    """预设做底, 已显式设置的选项保留 (返回新 dict, 不改入参)。"""
    preset = PROFILES.get(name, {})
    merged = dict(preset)
    merged.update({k: v for k, v in options.items() if v})
    return merged
