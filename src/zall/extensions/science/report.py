"""zall.extensions.science.report — 研究报告生成器 (Argus report_generator 对标)。

批跑结束后把 RunOutcome 汇总为:
  results/science/<topic>_<ts>/REPORT.md    人类可读 (tier 表 + 每模块依据)
  results/science/<topic>_<ts>/report.json  机器可读 (含链哈希锚点, 若在 agent 回合内)

与 Argus 的差异: Argus 报告是纯文本转储; zall 报告带**认识论分级表**和
verifiability timeline 锚点 — 报告本身可回溯到链哈希证据 (论文 artifact 纪律)。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

_TIER_MARK = {
    "proven": "PROVEN",
    "corroborated": "CORROBORATED",
    "refuted": "REFUTED",
    "unknown": "UNKNOWN",
    "error": "ERROR",
}


def _chain_anchor(state: dict[str, Any] | None) -> str:
    """当前 timeline 链尾 hash (loop 在场时); 无则空串 — 报告如实标注"无锚点"。"""
    if not state:
        return ""
    loop = state.get("_loop")
    recorder = getattr(loop, "_recorder", None) if loop is not None else None
    tail = getattr(recorder, "tail_hash", None)
    if isinstance(tail, str) and tail and set(tail) != {"0"}:
        return f"sha256:{tail[:16]}"
    return ""


def generate_report(
    outcomes: list[Any],
    topic: str,
    *,
    base_dir: Path | None = None,
    state: dict[str, Any] | None = None,
) -> Path | None:
    """写出 MD+JSON 报告, 返回目录路径; 无可写结果时返回 None。"""
    if not outcomes:
        return None
    ts = time.strftime("%Y%m%d_%H%M%S")
    safe_topic = "".join(c if c.isalnum() or c in "-_" else "_" for c in topic)[:40] or "run"
    out_dir = (base_dir or Path.cwd() / "results" / "science") / f"{safe_topic}_{ts}"
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None

    anchor = _chain_anchor(state)
    payload = {
        "generated_at": int(time.time()),
        "topic": topic,
        "chain_anchor": anchor,
        "runs": [
            {
                "module_id": o.module_id,
                "name": o.name,
                "tier": o.tier,
                "claim": o.claim,
                "detail": o.detail,
                "seconds": round(o.seconds, 3),
                "report_path": o.report_path,
                "options": o.options,
                "ok": o.ok,
            }
            for o in outcomes
        ],
    }
    try:
        (out_dir / "report.json").write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError:
        return None

    lines = [
        f"# Science Report — {topic}",
        "",
        f"- generated: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- chain anchor: {anchor or '(none — run outside an agent turn)'}",
        "",
        "| id | module | tier | seconds |",
        "|---|---|---|---|",
    ]
    for o in outcomes:
        lines.append(f"| {o.module_id} | {o.name} | {_TIER_MARK.get(o.tier, o.tier)} | {o.seconds:.2f} |")
    lines.append("")
    for o in outcomes:
        lines.append(f"## {o.module_id}. {o.name} — {_TIER_MARK.get(o.tier, o.tier)}")
        if o.claim:
            lines.append(f"- claim: {o.claim}")
        if o.detail:
            lines.append(f"- detail: {o.detail}")
        if o.report_path:
            lines.append(f"- artifact: {o.report_path}")
        if o.options:
            lines.append(f"- options: {json.dumps(o.options, ensure_ascii=False)}")
        lines.append("")
    lines.append(
        "> Tier semantics (Proof Gate): PROVEN = machine-checked universal proof; "
        "CORROBORATED = bounded search, **not a proof**; REFUTED = explicit counterexample; "
        "UNKNOWN = undetermined; ERROR = execution failure (not an epistemic state)."
    )
    try:
        (out_dir / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError:
        pass
    return out_dir
