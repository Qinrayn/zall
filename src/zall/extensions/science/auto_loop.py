"""zall.extensions.science.auto_loop — 自主研究循环 (混合自主, PARADIGM 猜想-反驳-蒸馏)。

自主性边界 (硬约束):
  - 执行/验证/分级/记账全部是确定性代码 (runner + proof_gate + ScienceStore, 零 token);
  - LLM 只出现在一处: 规则走投无路时 (REFUTED / UNKNOWN 扩界用尽) **在目录内**
    提议下一个模块+参数 — 输出 JSON, 严格校验 (模块必须在目录、选项必须在白名单,
    不合法直接丢弃回退规则), --budget 限次;
  - 无 adapter (未配模型) 自动降级纯规则状态机, 绝不阻塞;
  - CORROBORATED 永不冒充 PROVEN: 蒸馏走 experience_store.record_certificate,
    tier 墙由 store 强制 (Proof Gate §5.1 红线), 本模块不绕过。

IPR-3: 不 import 任何模型 SDK — adapter 由调用方 (loop.model_adapter) 注入。
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

_DEFAULT_BUDGET = 2
_DEFAULT_CYCLES = 6
_MAX_CYCLES_CAP = 12
_LLM_TIMEOUT_S = 60.0

# 规则扩界的键与上限 (UNKNOWN/CORROBORATED 时"再熬一轮"的确定性策略)
_EXPAND_RULES: dict[str, tuple[str, int, int]] = {
    # key: (乘数/加数模式, 倍数, 上限)
    "residual_bound": ("mul", 2, 100_000),
    "search_bound": ("mul", 4, 4_096),
    "coef_max": ("add", 2, 12),
}

_TIER_VALUE = {"proven": 1.0, "corroborated": 0.5, "refuted": 0.0}
_PROOF_THRESHOLD = 0.75

_PROPOSE_SYS = (
    "You are the hypothesis-proposal step of an autonomous math research loop. "
    "Pick the NEXT research module to run from the given catalog, with options. "
    "Reply with ONLY a JSON object: "
    '{"module": "<id or name>", "options": {"<opt>": "<value>"}, '
    '"rationale": "<one sentence>"} — no prose outside the JSON.'
)


def _extract_json(text: str) -> dict[str, Any] | None:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _bound_from_detail(detail: str) -> int | None:
    m = re.search(r"bound=(\d+)", detail or "")
    return int(m.group(1)) if m else None


def _claim_for(mod: Any, opts: dict[str, str]) -> str:
    spec = opts.get("congruences") or opts.get("preset") or opts.get("poly")
    tail = f" [input: {spec}]" if spec else ""
    return f"{mod.name}: {mod.description[:160]}{tail}"


def _provenance(protocol: str, mod: Any) -> Any:
    from zall._util.hash_utils import environment_hash, hash_bytes, hash_file
    from zall.core.provenance import ScienceProvenance

    code_path = ""
    if mod.script:
        candidate = Path(mod.script)
        code_path = str(candidate) if candidate.is_file() else ""
    if not code_path:
        from zall.extensions.science import runner as _r
        code_path = _r.__file__
    try:
        code_hash = hash_file(code_path)
    except OSError:
        code_hash = "sha256:unreadable-code"
    return ScienceProvenance(
        protocol_hash=hash_bytes(protocol.encode("utf-8")),
        data_hash="sha256:unspecified-data",
        analysis_code_hash=code_hash,
        environment_hash=environment_hash(),
    )


def _timeline_note(state: dict[str, Any] | None, **kw: Any) -> None:
    """把循环步骤记入链哈希 timeline (loop 在场时; 失败静默 IPR-0)。"""
    try:
        loop = (state or {}).get("_loop")
        rec = getattr(loop, "_recorder", None)
        if rec is None:
            return
        from zall.core.verifiability import EventType
        ts = int(time.time() * 1000)
        rec.append(
            event_id=f"sci_auto_{ts}",
            ts=ts,
            event_type=EventType.SYSTEM_INJECTION,
            payload={"reason": "science_auto", **kw},
        )
    except Exception:
        pass


def _expand_options(opts: dict[str, str]) -> dict[str, str] | None:
    """规则扩界: 有可扩展键且未到上限 → 返回新 opts; 否则 None (扩无可扩)。"""
    changed: dict[str, str] = {}
    for key, (mode, factor, cap) in _EXPAND_RULES.items():
        try:
            cur = int(float(opts.get(key, "0") or 0))
        except ValueError:
            continue
        if cur <= 0:
            continue
        nxt = cur * factor if mode == "mul" else cur + factor
        if nxt <= cap and nxt > cur:
            changed[key] = str(min(nxt, cap))
    if not changed:
        return None
    return {**opts, **changed}


def _rule_next(current: Any, catalog: list[Any], tried: set[str]) -> Any | None:
    """规则降级提议: 同 section 未试过的模块优先, 其次任意未试过。"""
    untried = [m for m in catalog if m.id not in tried]
    if not untried:
        return None
    same = [m for m in untried if current is not None and m.section == current.section]
    return (same or untried)[0]


def _llm_propose(
    adapter: Any,
    topic: str,
    catalog: list[Any],
    history: list[tuple[str, str]],
    tried: set[str],
    out: Any,
) -> tuple[Any, dict[str, str]] | None:
    """LLM 在目录内提议下一模块+参数; 输出严格校验, 不合法即弃 (回退规则)。"""
    from zall.cli.commands.btw import _complete_with_timeout
    from zall.cli.render import flash_info, flash_warn
    from zall.core.model import Message

    cat_lines = [
        f"- id={m.id} name={m.name} options={list(m.options)} "
        f"section={m.section} desc={m.description[:100]}"
        for m in catalog
    ]
    hist = "; ".join(f"{n}->{t}" for n, t in history) or "(none yet)"
    user = (
        f"Research topic: {topic or '(open)'}\n"
        f"Already tried: {hist}\n"
        f"Avoid repeating tried modules unless with materially different options.\n"
        f"Catalog:\n" + "\n".join(cat_lines) + "\n"
        "Pick the next module and its option values."
    )
    messages = [
        Message(role="system", content=_PROPOSE_SYS),
        Message(role="user", content=user),
    ]
    try:
        resp = _complete_with_timeout(adapter, messages, [], _LLM_TIMEOUT_S)
    except Exception as e:
        flash_warn(out, f"llm proposal failed ({e}) — falling back to rules")
        return None
    if resp is None:
        flash_warn(out, "llm proposal timed out — falling back to rules")
        return None
    data = _extract_json(str(getattr(resp, "content", "") or ""))
    if data is None:
        flash_warn(out, "llm proposal was not valid JSON — falling back to rules")
        return None
    ident = str(data.get("module", "")).strip()
    mod = None
    for m in catalog:
        if ident == m.id or ident.lower() == m.name.lower():
            mod = m
            break
    if mod is None:
        from zall.extensions.science.catalog import find_modules
        matches = find_modules(ident, catalog)
        mod = matches[0] if len(matches) == 1 else None
    if mod is None:
        flash_warn(out, f"llm proposed unknown module {ident!r} — rejected, falling back to rules")
        return None
    # 选项白名单校验 (防夹带): 只收该模块声明过的选项
    allowed = {o.replace("-", "_").lower() for o in mod.options}
    _raw = data.get("options")
    raw_opts: dict[Any, Any] = _raw if isinstance(_raw, dict) else {}
    opts: dict[str, str] = {}
    rejected: list[str] = []
    for k, v in raw_opts.items():
        nk = str(k).replace("-", "_").lower()
        if nk in allowed:
            opts[nk] = str(v)
        else:
            rejected.append(str(k))
    if rejected:
        flash_warn(out, f"llm proposal options rejected (not in catalog whitelist): {', '.join(rejected)}")
    rationale = str(data.get("rationale", "")).strip()
    flash_info(out, f"llm proposal: {mod.id}. {mod.name}" + (f" — {rationale}" if rationale else ""))
    return mod, opts


def _distill(mod: Any, outcome: Any, topic: str) -> None:
    """把裁定蒸馏进经验库 (tier 墙由 record_certificate 强制, 本处不绕过)。"""
    from zall.core.experience_store import get_experience_store

    cert = SimpleNamespace(
        tier=SimpleNamespace(value=outcome.tier),
        claim=outcome.claim or mod.name,
        detail=outcome.detail,
        bound=_bound_from_detail(outcome.detail),
    )
    try:
        get_experience_store().record_certificate(
            cert, task=topic or mod.name, source="science_auto")
    except Exception:
        pass  # 蒸馏失败不打断循环 (IPR-0)


def research_auto(args: list[str], out: Any, loop: Any | None,
                  state: dict[str, Any] | None) -> str:
    """/science auto <topic> [--budget N] [--cycles M] [--module <id>] 入口。"""
    from zall.cli.render import (
        flash_err,
        flash_info,
        flash_ok,
        flash_warn,
        kv_table,
        next_steps_panel,
        section_header,
    )
    from zall._util.hash_utils import hash_bytes
    from zall.core.experiment import ExperimentGoal
    from zall.core.evidence import Evidence, EvidenceType, NegativeResult
    from zall.core.hypothesis import Hypothesis, HypothesisStatus
    from zall.extensions.science import runner as sci_runner
    from zall.extensions.science.catalog import find_modules, load_catalog
    from zall.extensions.science.profiles import PROFILES, apply_profile
    from zall.extensions.science.state import get_science_state
    from zall.extensions.science.store import ScienceStore

    # ── 参数 ──
    budget = _DEFAULT_BUDGET
    max_cycles = _DEFAULT_CYCLES
    seed: str | None = None
    positional: list[str] = []
    i = 0
    while i < len(args):
        tok = args[i]
        if tok == "--budget" and i + 1 < len(args):
            budget = max(0, int(args[i + 1]) if args[i + 1].isdigit() else _DEFAULT_BUDGET)
            i += 2
            continue
        if tok == "--cycles" and i + 1 < len(args):
            max_cycles = min(_MAX_CYCLES_CAP,
                             max(1, int(args[i + 1]) if args[i + 1].isdigit() else _DEFAULT_CYCLES))
            i += 2
            continue
        if tok == "--module" and i + 1 < len(args):
            seed = args[i + 1]
            i += 2
            continue
        positional.append(tok)
        i += 1
    topic = " ".join(positional).strip()
    initial_budget = budget
    if not topic and not seed:
        flash_err(out, "usage: /science auto <topic> [--budget N] [--cycles M] [--module <id>]")
        return "handled"

    sst = get_science_state()
    catalog = load_catalog()
    adapter = getattr(loop, "model_adapter", None) if loop is not None else None
    store = (state or {}).get("_science_store") or ScienceStore()
    timeout = int(PROFILES.get(sst.profile, {}).get("timeout", "900"))

    # ── 种子模块 ──
    mod: Any = None
    if seed:
        matches = find_modules(seed, catalog)
        mod = matches[0] if matches else None
        if mod is None:
            flash_err(out, f"seed module {seed!r} not in catalog")
            return "handled"
    elif topic:
        matches = find_modules(topic, catalog)
        mod = matches[0] if matches else None
    if mod is None and adapter is not None and budget > 0:
        proposal = _llm_propose(adapter, topic, catalog, [], set(), out)
        budget -= 1
        if proposal is not None:
            mod, extra = proposal
            sst.module_options.setdefault(mod.id, {}).update(extra)
    if mod is None:
        flash_err(out, f"no catalog module matches {topic or seed!r} (and no LLM proposal available)")
        next_steps_panel(out, ["/science modules — see what IS verifiable today"])
        return "handled"

    llm_used = 0
    cycles = 0
    expansions = 0
    tried: set[str] = set()
    history: list[tuple[str, str]] = []
    hyp_short: list[str] = []
    final_tier = "unknown"
    distilled = 0

    flash_info(out, f"auto research: topic={topic or '-'} · seed={mod.id}. {mod.name} · "
                    f"budget={budget} llm call(s) · max {max_cycles} cycles · profile '{sst.profile}'")

    while cycles < max_cycles:
        cycles += 1
        tried.add(mod.id)
        opts = apply_profile(sst.profile, sst.merged_options(mod.id))

        # ── 记账: 假设 (锁定后不可改, I-1) + 实验 ──
        claim = _claim_for(mod, opts)
        h = Hypothesis(
            id=uuid4(),
            claim=claim,
            prediction="machine-checkable certificate reaches PROVEN "
                       "(or CORROBORATED with an explicit bound — never conflated)",
            confidence=0.5,
            created_by="science-auto",
            created_at=int(time.time()),
        )
        h.lock()
        store.add_hypothesis(h)
        hyp_short.append(str(h.id)[:8])
        protocol = json.dumps(
            {"module": mod.id, "name": mod.name,
             "mode": "certifier" if mod.is_in_process else "script",
             "options": opts},
            sort_keys=True, ensure_ascii=False)
        exp = ExperimentGoal(
            id=uuid4(),
            hypothesis_id=h.id,
            protocol=protocol,
            data_snapshot=hash_bytes(protocol.encode("utf-8")),
        )
        store.add_experiment(exp)
        exp.start()
        store.update_experiment(exp)

        section_header(out, f"Cycle {cycles}: {mod.name} ({mod.id})")
        o = sci_runner.run_module(mod, opts, out=out, timeout=timeout, stream=True)
        history.append((mod.name, o.tier))
        final_tier = o.tier
        _timeline_note(state, cycle=cycles, module=mod.id, tier=o.tier)

        if o.is_error:
            exp.fail(o.detail or "execution error")
            store.update_experiment(exp)
            flash_err(out, f"{mod.name} → ERROR: {o.detail}")
            break
        exp.complete({"tier": o.tier, "detail": o.detail, "seconds": round(o.seconds, 3)})
        store.update_experiment(exp)

        # ── 证据记账 (REFUTED 走 NegativeResult 一等公民; UNKNOWN 不记账 — 未定不是反证) ──
        if o.tier in _TIER_VALUE:
            ev_type = {
                "proven": EvidenceType.POSITIVE,
                "corroborated": EvidenceType.INCONCLUSIVE,
                "refuted": EvidenceType.NEGATIVE,
            }[o.tier]
            ev = Evidence(
                id=uuid4(),
                hypothesis_id=h.id,
                experiment_id=exp.id,
                type=ev_type,
                metric="verification_tier",
                value=_TIER_VALUE[o.tier],
                threshold=_PROOF_THRESHOLD,
                supports=(o.tier != "refuted"),
                provenance=_provenance(protocol, mod),
                created_at=int(time.time()),
            )
            if o.tier == "refuted":
                store.add_evidence(NegativeResult(
                    evidence=ev,
                    what_failed=o.claim or mod.name,
                    conditions=dict(opts),
                    diagnosis=o.detail or "explicit counterexample exhibited",
                    value="excluded this universal claim — counterexample is machine-checkable",
                ))
            else:
                store.add_evidence(ev)
            h.add_evidence(ev.id, supports=(o.tier != "refuted"))
            if o.tier == "refuted":
                h.falsify(ev.id)
            elif o.tier == "proven":
                h.status = HypothesisStatus.CONFIRMED  # 机器证明 → 假设确认
            store.update_hypothesis(h)

        # ── 路由 (规则优先, LLM 只在规则走投无路时出场) ──
        if o.tier == "proven":
            _distill(mod, o, topic)
            distilled += 1
            flash_ok(out, f"PROVEN — {mod.name}; certificate distilled into the experience store")
            break

        if o.tier == "corroborated":
            expanded = _expand_options(opts) if expansions < 2 else None
            if expanded is not None:
                expansions += 1
                sst.module_options.setdefault(mod.id, {}).update(
                    {k: v for k, v in expanded.items() if k in _EXPAND_RULES})
                sst.save()
                flash_info(out, f"CORROBORATED — expanding bounds "
                                f"({', '.join(f'{k}={v}' for k, v in expanded.items() if k in _EXPAND_RULES)}) "
                                f"and re-running (corroboration is NOT proof)")
                continue
            _distill(mod, o, topic)
            distilled += 1
            flash_info(out, "CORROBORATED within bound — distilled WITH its bound "
                            "(the tier wall keeps it out of the PROVEN pool)")
            break

        # REFUTED / UNKNOWN → 换下一模块 (LLM 提议优先, 预算尽则规则)
        if o.tier == "refuted":
            flash_warn(out, f"REFUTED — {mod.name}; counterexample recorded as a first-class NegativeResult")
        else:
            expanded = _expand_options(opts) if expansions < 2 else None
            if expanded is not None:
                expansions += 1
                sst.module_options.setdefault(mod.id, {}).update(
                    {k: v for k, v in expanded.items() if k in _EXPAND_RULES})
                sst.save()
                flash_info(out, "UNKNOWN — expanding bounds and re-running")
                continue
            flash_warn(out, f"UNKNOWN — bounds exhausted for {mod.name}")

        nxt: tuple[Any, dict[str, str]] | None = None
        if adapter is not None and llm_used < budget:
            llm_used += 1
            budget -= 1
            nxt = _llm_propose(adapter, topic, catalog, history, tried, out)
        if nxt is None:
            rule_mod = _rule_next(mod, catalog, tried)
            nxt = (rule_mod, {}) if rule_mod is not None else None
            if rule_mod is not None:
                flash_info(out, f"rule fallback: next untried module {rule_mod.id}. {rule_mod.name}")
        if nxt is None:
            flash_info(out, "catalog exhausted — stopping honestly (no moves left)")
            break
        mod, extra = nxt
        if extra:
            sst.module_options.setdefault(mod.id, {}).update(extra)
            sst.save()

    # ── 总结 ──
    section_header(out, "Auto Research Summary")
    kv_table(out, topic or "auto", [
        ("cycles", str(cycles)),
        ("llm calls", f"{llm_used}/{initial_budget}"),
        ("final tier", final_tier.upper()),
        ("distilled", str(distilled)),
        ("hypotheses", ", ".join(hyp_short) or "None"),
        ("trail", " → ".join(f"{n}:{t}" for n, t in history) or "None"),
    ])
    next_steps_panel(out, [
        "/science list — inspect the hypothesis ledger",
        "/science report auto — write the run report",
        "/science modules — extend the catalog with new certifiers",
    ])
    return "handled"
