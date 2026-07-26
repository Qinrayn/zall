"""zall.core.experience_store — 持久经验流 + 技能蒸馏 + recall (PARADIGM.md Step 1)。

可证伪经验机的"经验流"与"技能记忆"两个器官的落地:
  - 跨会话持久化每个任务的结果 (~/.zall/experience/experience.jsonl)。
  - **Popperian Gate (分层)**: 只有可蒸馏层 (PROVEN 机器已证明 / CORROBORATED 界内佐证)
    的经验才进技能召回池; 且机器强制那道墙——CORROBORATED 永不当 PROVEN 复用/渲染
    (对接 core.proof_gate.VerificationTier)。
  - recall(task): 新任务开始时按关键词相关性召回过往**已验证**技能, 注入 prompt →
    同类任务跨会话复利 (真正"越用越强")。

为什么关键词而非向量检索: 保持**模型无关 + 离线可跑 + 确定性可测** (IPR-3)。
向量检索需要 embedding 模型, 与 zall 模型无关/可离线的定位冲突, 留待可选增强。

IPR constraints:
  IPR-0: 加载/保存失败静默降级, 不阻塞主流程; 不变量测试见
         tests/test_experience_store_invariants.py (含反例)。
  IPR-3: stdlib + json only, 无模型 SDK。
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# 上限: 防经验库无界增长 (超出后淘汰最旧的未验证记录, 尽量保留 verified 技能)
MAX_RECORDS = 500
# 召回默认条数
DEFAULT_RECALL_K = 3

# 认识论分层 (对接 core.proof_gate.VerificationTier)。学习门只蒸馏"可蒸馏"层,
# 并机器强制那道墙: CORROBORATED (界内佐证) 永不当 PROVEN (已证明) 复用/渲染。
TIER_PROVEN = "proven"
TIER_CORROBORATED = "corroborated"
TIER_REFUTED = "refuted"
TIER_UNKNOWN = "unknown"
DISTILLABLE_TIERS = frozenset({TIER_PROVEN, TIER_CORROBORATED})

# 极小停用词表 (关键词提取用; 保持轻量, 不追求完备)
_STOPWORDS = frozenset({
    "the", "a", "an", "and", "or", "but", "for", "to", "of", "in", "on", "at",
    "is", "are", "be", "with", "that", "this", "it", "as", "by", "from", "into",
    "then", "you", "your", "please", "make", "create", "write", "add", "use",
    "using", "func", "def", "return", "file", "code", "task", "run", "should",
    "把", "的", "了", "一个", "个", "和", "用", "写", "加", "让", "请", "做",
})

_TOKEN_RE = re.compile(r"[a-zA-Z_][a-zA-Z0-9_]{2,}|[\u4e00-\u9fff]{2,}")


def extract_keywords(text: str, limit: int = 12) -> tuple[str, ...]:
    """从文本提取关键词 (小写英文标识符/词 + CJK 词组), 去停用词与重复, 保序。

    确定性: 相同输入 → 相同输出 (可测)。
    """
    seen: set[str] = set()
    out: list[str] = []
    for m in _TOKEN_RE.finditer(text or ""):
        tok = m.group(0).lower()
        if tok in _STOPWORDS or tok in seen:
            continue
        seen.add(tok)
        out.append(tok)
        if len(out) >= limit:
            break
    return tuple(out)


def _experience_path() -> Path:
    from zall.safety.config import CONFIG_DIR
    return Path(str(CONFIG_DIR)) / "experience" / "experience.jsonl"


@dataclass(frozen=True)
class ExperienceRecord:
    """一条经验: 任务 + 结果 + 是否熬过证伪 (verified)。

    verified=True 的记录才是"技能"(可被 recall 复用); False 只作为历史/失败教训。
    """

    task: str
    outcome: str
    verified: bool
    score: float = 1.0
    keywords: tuple[str, ...] = field(default_factory=tuple)
    source: str = "agent"
    ts: float = 0.0
    tier: str = ""            # 认识论层 (proven/corroborated/refuted/unknown); 空=从 verified 推导
    bound: int | None = None  # CORROBORATED 的界 (如 "n<bound"); 其它层为 None

    @property
    def effective_tier(self) -> str:
        """规范化认识论层: 显式 tier 优先; 否则由 verified 推导 (向后兼容旧记录)。"""
        if self.tier:
            return self.tier
        return TIER_CORROBORATED if self.verified else TIER_UNKNOWN

    def to_json(self) -> dict[str, Any]:
        return {
            "task": self.task, "outcome": self.outcome, "verified": self.verified,
            "score": self.score, "keywords": list(self.keywords),
            "source": self.source, "ts": self.ts,
            "tier": self.tier, "bound": self.bound,
        }

    @staticmethod
    def from_json(d: dict[str, Any]) -> ExperienceRecord:
        b = d.get("bound")
        return ExperienceRecord(
            task=str(d.get("task", "")),
            outcome=str(d.get("outcome", "")),
            verified=bool(d.get("verified", False)),
            score=float(d.get("score", 1.0) or 0.0),
            keywords=tuple(d.get("keywords", []) or ()),
            source=str(d.get("source", "agent")),
            ts=float(d.get("ts", 0.0) or 0.0),
            tier=str(d.get("tier", "") or ""),
            bound=int(b) if isinstance(b, (int, float)) else None,
        )


class ExperienceStore:
    """跨会话持久经验流 + 技能召回。

    record → (Popperian Gate) 仅 verified 进技能 → recall 按关键词相关性召回 → 复利。
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or _experience_path()
        self._records: list[ExperienceRecord] = []
        self._loaded = False

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        try:
            if self._path.exists():
                with open(self._path, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            self._records.append(ExperienceRecord.from_json(json.loads(line)))
                        except (json.JSONDecodeError, TypeError, ValueError):
                            continue
        except OSError:
            pass  # IPR-0: 静默降级

    def record(
        self, task: str, outcome: str, *, verified: bool | None = None,
        score: float = 1.0, source: str = "agent",
        tier: str = "", bound: int | None = None,
    ) -> ExperienceRecord | None:
        """记录一条经验。空任务/空结果不记录; 同 (task, outcome) 去重 (保留已有)。

        认识论门: tier 给定时以 tier 为准 (verified = tier ∈ DISTILLABLE_TIERS);
        否则由 verified 推导 tier (向后兼容)。只有可蒸馏层进技能召回池。
        """
        task = (task or "").strip()
        outcome = (outcome or "").strip()
        if not task or not outcome:
            return None
        tier = (tier or "").strip().lower()
        if tier:
            verified_final = tier in DISTILLABLE_TIERS
        elif verified is not None:
            verified_final = bool(verified)
            tier = TIER_CORROBORATED if verified_final else TIER_UNKNOWN
        else:
            verified_final, tier = False, TIER_UNKNOWN
        self._ensure_loaded()
        for r in self._records:
            if r.task == task and r.outcome == outcome:
                return r  # 去重: 已存在
        rec = ExperienceRecord(
            task=task, outcome=outcome, verified=verified_final,
            score=float(score), keywords=extract_keywords(task + " " + outcome),
            source=source, ts=time.time(), tier=tier, bound=bound,
        )
        self._records.append(rec)
        self._evict_if_needed()
        self._save()
        return rec

    def _evict_if_needed(self) -> None:
        """超上限时淘汰: 优先淘汰最旧的**未验证**记录, 尽量保留 verified 技能。"""
        if len(self._records) <= MAX_RECORDS:
            return
        unverified = [r for r in self._records if not r.verified]
        verified = [r for r in self._records if r.verified]
        # 先砍未验证 (按 ts 旧→新)
        unverified.sort(key=lambda r: r.ts)
        keep_unverified = unverified[max(0, len(self._records) - MAX_RECORDS):]
        merged = verified + keep_unverified
        if len(merged) > MAX_RECORDS:
            # verified 也超了: 保留分高/近的
            merged.sort(key=lambda r: (r.score, r.ts), reverse=True)
            merged = merged[:MAX_RECORDS]
        # 恢复时间顺序 (稳定)
        merged.sort(key=lambda r: r.ts)
        self._records = merged

    def recall(
        self, task: str, k: int = DEFAULT_RECALL_K, *, verified_only: bool = True,
    ) -> list[ExperienceRecord]:
        """按关键词相关性召回与 task 最相关的经验 (默认只召回 verified 技能)。

        排序: (关键词重叠数 desc, score desc, ts desc)。重叠为 0 的不返回 (不注入噪声)。
        确定性: 相同库 + 相同 task → 相同结果。
        """
        self._ensure_loaded()
        q = set(extract_keywords(task))
        if not q:
            return []
        pool = [r for r in self._records if (r.verified or not verified_only)]
        scored: list[tuple[int, float, float, ExperienceRecord]] = []
        for r in pool:
            overlap = len(q & set(r.keywords))
            if overlap > 0:
                scored.append((overlap, r.score, r.ts, r))
        scored.sort(key=lambda t: (t[0], t[1], t[2]), reverse=True)
        return [t[3] for t in scored[:max(0, k)]]

    def skills(self) -> list[ExperienceRecord]:
        """所有可蒸馏技能 (verified=True, 即 PROVEN ∪ CORROBORATED)。"""
        self._ensure_loaded()
        return [r for r in self._records if r.verified]

    def proven_skills(self) -> list[ExperienceRecord]:
        """仅**机器已证明**的技能 (tier=PROVEN)。那道墙: 佐证不在此列。"""
        self._ensure_loaded()
        return [r for r in self._records if r.effective_tier == TIER_PROVEN]

    def corroborated_skills(self) -> list[ExperienceRecord]:
        """仅**界内佐证**的技能 (tier=CORROBORATED, 未证明)。"""
        self._ensure_loaded()
        return [r for r in self._records if r.effective_tier == TIER_CORROBORATED]

    def record_certificate(
        self, certificate: Any, task: str, *, outcome: str | None = None,
        source: str = "proof_gate",
    ) -> ExperienceRecord | None:
        """桥接: 把 proof_gate 的 ProofCertificate 按其 tier 记入学习门。

        鸭子类型 (不 import proof_gate, 避免耦合): certificate 需有 .tier(.value)/
        .detail/.claim/.bound。REFUTED/UNKNOWN 记为非技能 (不召回); PROVEN/CORROBORATED
        进技能池, 且 CORROBORATED 携带其 bound —— 那道墙由 tier 落到本记录。
        """
        tier_obj = getattr(certificate, "tier", None)
        tier = str(getattr(tier_obj, "value", tier_obj) or "").lower()
        body = outcome or getattr(certificate, "detail", "") or getattr(certificate, "claim", "")
        bound = getattr(certificate, "bound", None)
        return self.record(task, body, tier=tier, bound=bound, source=source)

    def build_recall_context(self, task: str, k: int = DEFAULT_RECALL_K) -> str:
        """为 task 构造可注入 prompt 的"过往技能"段; 无相关则返回空串。

        每条按认识论层标注: [PROVEN] = 机器已证明; [CORROBORATED n<N] = 界内佐证
        (未证明)。这道标注把那面墙落到 prompt —— 模型看到的佐证绝不冒充证明。
        """
        hits = self.recall(task, k=k, verified_only=True)
        if not hits:
            return ""
        lines = [
            "",
            "LEARNED SKILLS (tier-gated; PROVEN = machine-proven, "
            "CORROBORATED = bounded, NOT proven - reuse accordingly):",
        ]
        for r in hits:
            summary = r.outcome.replace("\n", " ").strip()
            if len(summary) > 240:
                summary = summary[:240] + "…"
            et = r.effective_tier
            if et == TIER_CORROBORATED and r.bound is not None:
                label = f"[CORROBORATED n<{r.bound}]"
            else:
                label = f"[{et.upper()}]"
            lines.append(f"  - {label} ({r.task[:60]}): {summary}")
        return "\n".join(lines)

    def stats(self) -> dict[str, int]:
        self._ensure_loaded()
        return {
            "total": len(self._records),
            "verified": sum(1 for r in self._records if r.verified),
            "unverified": sum(1 for r in self._records if not r.verified),
            "proven": sum(1 for r in self._records if r.effective_tier == TIER_PROVEN),
            "corroborated": sum(1 for r in self._records if r.effective_tier == TIER_CORROBORATED),
        }

    def clear(self) -> None:
        self._records.clear()
        self._loaded = True
        try:
            if self._path.exists():
                self._path.unlink()
        except OSError:
            pass

    def _save(self) -> bool:
        """全量原子重写 JSONL (dedup/evict 后; 崩溃不丢已有数据)。"""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.parent / f".experience_{os.getpid()}.tmp"
            with open(tmp, "w", encoding="utf-8", newline="") as f:
                f.writelines(json.dumps(r.to_json(), ensure_ascii=False) + "\n" for r in self._records)
                f.flush()
                os.fsync(f.fileno())
            os.replace(str(tmp), str(self._path))
            return True
        except OSError:
            return False


# 全局单例
_global_store: ExperienceStore | None = None


def get_experience_store() -> ExperienceStore:
    global _global_store
    if _global_store is None:
        _global_store = ExperienceStore()
    return _global_store


def reset_experience_store() -> None:
    """测试隔离用: 重置全局单例。"""
    global _global_store
    _global_store = None
