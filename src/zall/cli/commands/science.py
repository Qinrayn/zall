"""zall.cli.commands.science - Science Kit CLI commands (E3).

Corresponds to:
  MASTER.md §12.3 E3 (Science Kit 首个闭环)
  docs/E3_SCIENCE_KIT.md §3 (CLI)

Commands (hypothesis lifecycle, E3):
  /science new "claim" --prediction "..."   create a hypothesis
  /science list                              list all hypotheses
  /science show <id>                         show hypothesis + evidence
  /science evidence <hid> --metric M --value V [--threshold T] --supports/--against
  /science falsify <hid> --evidence <eid> --diagnosis "..." --value "..."
  /science revise <hid> --claim "..." --prediction "..."

Research workbench (Argus 吸纳轮 — 数据驱动目录 + 控制台工作流):
  /science modules [-s|-d|-t] [section|tag:x]   browse the research catalog
  /science use <id|name|tmp#>                   select a module (info table + next steps)
  /science set <k=v | k v | id k v>             set options (profile < global < module)
  /science unset <k | id k>                     remove an option
  /science run [ids...] [--dry-run] [--timeout N]  execute (batch progress + tier results)
  /science report [topic]                       write REPORT.md + report.json (chain anchor)
  /science profile [quick|deep|exhaustive]      research-depth presets
  /science fav add|del|run|list|clear|tag:<t>   favorites
  /science recent                               recent modules + use counts
  /science auto <topic> [--budget N]            autonomous conjecture-refute-distill loop

IPR constraints:
  IPR-3: only stdlib + rich + zall core, no model SDK
"""

from __future__ import annotations

import argparse
import shlex
import time
from typing import Any
from uuid import UUID, uuid4

from rich.table import Table

from zall.cli.commands._common import slash_command
from zall.cli.render import _shared_console
from zall.core.evidence import Evidence, EvidenceType, NegativeResult
from zall.core.hypothesis import Hypothesis
from zall.core.provenance import ScienceProvenance
from zall.extensions.science.store import ScienceStore

_CATEGORY_SCIENCE = "Science"


def _get_store(state: dict[str, Any] | None) -> ScienceStore:
    """Get ScienceStore from state, or create default one.

    state may carry '_science_store' (for testing). Otherwise default to
    ~/.zall/science/.
    """
    if state and "_science_store" in state:
        return state["_science_store"]
    return ScienceStore()


def _parse_uuid(s: str) -> UUID | None:
    try:
        return UUID(s)
    except (ValueError, AttributeError):
        return None


def _short_id(u: UUID | str) -> str:
    """Short 8-char id for display."""
    s = str(u)
    return s[:8] if len(s) >= 8 else s


def _build_provenance(
    protocol_path: str = "",
    data_path: str = "",
    code_path: str = "",
) -> ScienceProvenance:
    """Part D (真溯源): 用真实文件 SHA-256 构造 ScienceProvenance。

    提供路径则算真实哈希; 不提供则降级为带标记占位符 "sha256:unspecified-<field>",
    明确表示该字段未溯源 (而非伪装成已哈希的 "sha256:cli-manual")。环境哈希始终
    真实 (python 版本等, 无需用户提供文件)。
    """
    from zall._util.hash_utils import environment_hash, hash_file

    def _h(path: str, field: str) -> str:
        if path:
            try:
                return hash_file(path)
            except OSError:
                return f"sha256:unreadable-{field}"
        return f"sha256:unspecified-{field}"

    return ScienceProvenance(
        protocol_hash=_h(protocol_path, "protocol"),
        data_hash=_h(data_path, "data"),
        analysis_code_hash=_h(code_path, "code"),
        environment_hash=environment_hash(),
    )


@slash_command("/science", description="science kit: hypotheses, evidence, research workbench (modules/use/run), auto loop", category=_CATEGORY_SCIENCE)
def cmd_science(arg: str, out: Any, loop: Any | None = None, state: dict[str, Any] | None = None) -> str:
    """Science Kit 主命令 (E3, §12.3)."""
    parts = shlex.split(arg) if arg else []
    subcmd = parts[0] if parts else "list"

    store = _get_store(state)

    if subcmd in ("list", "ls"):
        return _sci_list(out, store)
    elif subcmd == "new":
        return _sci_new(parts[1:], out, store)
    elif subcmd == "show":
        return _sci_show(parts[1:], out, store)
    elif subcmd == "evidence":
        return _sci_evidence(parts[1:], out, store)
    elif subcmd == "falsify":
        return _sci_falsify(parts[1:], out, store)
    elif subcmd == "revise":
        return _sci_revise(parts[1:], out, store)
    # ── Argus 吸纳轮: 科研工作台 (目录/use/set/run 控制台流) ──
    elif subcmd in ("modules", "mods"):
        return _sci_modules(parts[1:], out)
    elif subcmd == "use":
        return _sci_use(parts[1:], out, state)
    elif subcmd == "set":
        return _sci_set(parts[1:], out, state)
    elif subcmd == "unset":
        return _sci_unset(parts[1:], out, state)
    elif subcmd == "run":
        return _sci_run(parts[1:], out, state)
    elif subcmd == "runall":
        return _sci_runall(parts[1:], out, state)
    elif subcmd == "last":
        return _sci_last(out, state)
    elif subcmd == "report":
        return _sci_report(parts[1:], out, state)
    elif subcmd == "profile":
        return _sci_profile(parts[1:], out, state)
    elif subcmd == "fav":
        return _sci_fav(parts[1:], out, state)
    elif subcmd == "recent":
        return _sci_recent(out, state)
    elif subcmd == "auto":
        return _sci_auto(parts[1:], out, loop, state)
    else:
        out.write("  usage: /science new|list|show|evidence|falsify|revise\n")
        out.write("         modules|use|set|run|report|profile|fav|recent|auto\n")
        out.write("  see /advanced for details\n")
        return "handled"


def _sci_list(out: Any, store: ScienceStore) -> str:
    hyps = store.list_hypotheses()
    if not hyps:
        out.write("  no hypotheses yet. try: /science new \"my claim\" --prediction \"...\"\n")
        return "handled"
    table = Table(title=f"Hypotheses ({len(hyps)})")
    table.add_column("id", style="cyan", no_wrap=True)
    table.add_column("v", justify="right")
    table.add_column("status", style="yellow")
    table.add_column("claim", overflow="fold")
    table.add_column("for", justify="right")
    table.add_column("against", justify="right")
    for h in hyps:
        table.add_row(
            _short_id(h.id),
            str(h.version),
            h.status.value,
            h.claim[:60],
            str(len(h.evidence_for)),
            str(len(h.evidence_against)),
        )
    console = _shared_console(out)
    console.print(table)
    return "handled"


def _sci_new(args: list[str], out: Any, store: ScienceStore) -> str:
    """Create a new hypothesis: /science new "claim" --prediction "..."."""
    parser = argparse.ArgumentParser(prog="/science new", add_help=False)
    parser.add_argument("claim")
    parser.add_argument("--prediction", required=True)
    parser.add_argument("--confidence", type=float, default=0.5)
    try:
        ns = parser.parse_args(args)
    except SystemExit:
        out.write('  usage: /science new "claim text" --prediction "prediction"\n')
        return "handled"

    h = Hypothesis(
        id=uuid4(),
        claim=ns.claim,
        prediction=ns.prediction,
        confidence=ns.confidence,
        created_by="cli",
        created_at=int(time.time()),
    )
    store.add_hypothesis(h)
    out.write(f"  \u2713 hypothesis created: {h.id}\n")
    out.write(f"    claim:      {h.claim}\n")
    out.write(f"    prediction: {h.prediction}\n")
    out.write(f"    status:     {h.status.value}\n")
    return "handled"


def _sci_show(args: list[str], out: Any, store: ScienceStore) -> str:
    if not args:
        out.write("  usage: /science show <id>\n")
        return "handled"
    hid = _parse_uuid(args[0])
    if hid is None:
        out.write(f"  invalid id: {args[0]}\n")
        return "handled"
    h = store.get_hypothesis(hid)
    if h is None:
        out.write(f"  hypothesis not found: {args[0]}\n")
        return "handled"
    out.write(f"  Hypothesis {h.id}  (v{h.version})\n")
    out.write(f"    status:      {h.status.value}\n")
    out.write(f"    claim:       {h.claim}\n")
    out.write(f"    prediction:  {h.prediction}\n")
    out.write(f"    confidence:  {h.confidence}\n")
    if h.revised_from:
        out.write(f"    revised_from: {h.revised_from}\n")
    if h.falsified_by:
        out.write(f"    falsified_by: {h.falsified_by}\n")
    evs = store.list_evidence(hid)
    if evs:
        out.write(f"    evidence ({len(evs)}):\n")
        for ev in evs:
            tag = "+" if ev.supports else "-"
            etype = ev.type.value
            out.write(f"      [{tag}] {_short_id(ev.id)} {etype} {ev.metric}={ev.value}")
            if ev.threshold is not None:
                out.write(f" (thresh {ev.threshold})")
            out.write("\n")
    else:
        out.write("    evidence: (none)\n")
    return "handled"


def _sci_evidence(args: list[str], out: Any, store: ScienceStore) -> str:
    """Record evidence: /science evidence <hid> --metric M --value V [--threshold T] --supports/--against."""
    parser = argparse.ArgumentParser(prog="/science evidence", add_help=False)
    parser.add_argument("hypothesis_id")
    parser.add_argument("--metric", required=True)
    parser.add_argument("--value", type=float, required=True)
    parser.add_argument("--threshold", type=float, default=None)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--supports", action="store_true")
    group.add_argument("--against", action="store_true")
    parser.add_argument("--negative", action="store_true",
                        help="mark as negative result (I-10: equally valued)")
    parser.add_argument("--diagnosis", default="")
    parser.add_argument("--what-failed", default="", dest="what_failed")
    parser.add_argument("--value-desc", default="", dest="value_desc",
                        help="scientific value of a negative result")
    # Part D (真溯源): 可选文件路径, 提供则算真实 SHA-256; 不提供则降级为标记占位符。
    parser.add_argument("--protocol", default="",
                        help="experiment script path (hashed for protocol_hash)")
    parser.add_argument("--data", default="",
                        help="input data path (hashed for data_hash)")
    parser.add_argument("--code", default="",
                        help="analysis code path (hashed for analysis_code_hash)")
    try:
        ns = parser.parse_args(args)
    except SystemExit:
        out.write("  usage: /science evidence <hid> --metric M --value V "
                  "[--threshold T] --supports|--against [--negative ...]\n")
        return "handled"

    hid = _parse_uuid(ns.hypothesis_id)
    if hid is None:
        out.write(f"  invalid hypothesis id: {ns.hypothesis_id}\n")
        return "handled"
    h = store.get_hypothesis(hid)
    if h is None:
        out.write(f"  hypothesis not found: {ns.hypothesis_id}\n")
        return "handled"

    # Part D (真溯源): 真实文件 SHA-256 (有路径时), 否则降级为带标记的占位符。
    # 占位符从 "sha256:cli-manual" 改为 "sha256:unspecified-<field>", 明确表示未溯源。
    prov = _build_provenance(
        protocol_path=ns.protocol, data_path=ns.data, code_path=ns.code,
    )
    supports = ns.supports
    ev_type = EvidenceType.POSITIVE if supports else EvidenceType.NEGATIVE
    ev = Evidence(
        id=uuid4(),
        hypothesis_id=hid,
        experiment_id=uuid4(),  # standalone evidence; experiment optional
        type=ev_type,
        metric=ns.metric,
        value=ns.value,
        threshold=ns.threshold,
        supports=supports,
        provenance=prov,
        created_at=int(time.time()),
    )

    if ns.negative or (not supports and ns.what_failed):
        nr = NegativeResult(
            evidence=ev,
            what_failed=ns.what_failed or "not specified",
            conditions={"metric": ns.metric, "value": ns.value},
            diagnosis=ns.diagnosis,
            value=ns.value_desc or "excluded a possibility",
        )
        store.add_evidence(nr)
    else:
        store.add_evidence(ev)

    # link evidence to hypothesis
    h.add_evidence(ev.id, supports=supports)
    store.update_hypothesis(h)

    tag = "supports" if supports else "against"
    out.write(f"  \u2713 evidence recorded ({tag}): {ev.id}\n")
    out.write(f"    {ns.metric} = {ns.value}")
    if ns.threshold is not None:
        out.write(f" (threshold {ns.threshold})")
    out.write("\n")
    return "handled"


def _sci_falsify(args: list[str], out: Any, store: ScienceStore) -> str:
    """Falsify a hypothesis: /science falsify <hid> --evidence <eid> --diagnosis "..." --value "..."."""
    parser = argparse.ArgumentParser(prog="/science falsify", add_help=False)
    parser.add_argument("hypothesis_id")
    parser.add_argument("--evidence", required=True)
    parser.add_argument("--diagnosis", required=True)
    parser.add_argument("--value", required=True,
                        help="scientific value: what possibility is excluded")
    try:
        ns = parser.parse_args(args)
    except SystemExit:
        out.write("  usage: /science falsify <hid> --evidence <eid> "
                  "--diagnosis \"...\" --value \"...\"\n")
        return "handled"

    hid = _parse_uuid(ns.hypothesis_id)
    eid = _parse_uuid(ns.evidence)
    if hid is None or eid is None:
        out.write("  invalid id\n")
        return "handled"
    h = store.get_hypothesis(hid)
    if h is None:
        out.write("  hypothesis not found\n")
        return "handled"
    # ensure evidence is in evidence_against (falsify requires it, H-3)
    if eid not in h.evidence_against:
        out.write(f"  evidence {ns.evidence} not in evidence_against; "
                  "record it with --against first (H-3 invariant)\n")
        return "handled"
    try:
        h.falsify(eid)
    except ValueError as e:
        out.write(f"  cannot falsify: {e}\n")
        return "handled"
    store.update_hypothesis(h)
    out.write(f"  \u2713 hypothesis falsified: {h.id}\n")
    out.write(f"    diagnosis: {ns.diagnosis}\n")
    out.write(f"    value:     {ns.value}\n")
    return "handled"


def _sci_revise(args: list[str], out: Any, store: ScienceStore) -> str:
    """Revise a falsified hypothesis: /science revise <hid> --claim "..." --prediction "..."."""
    parser = argparse.ArgumentParser(prog="/science revise", add_help=False)
    parser.add_argument("hypothesis_id")
    parser.add_argument("--claim", required=True)
    parser.add_argument("--prediction", required=True)
    try:
        ns = parser.parse_args(args)
    except SystemExit:
        out.write('  usage: /science revise <hid> --claim "..." --prediction "..."\n')
        return "handled"

    hid = _parse_uuid(ns.hypothesis_id)
    if hid is None:
        out.write(f"  invalid id: {ns.hypothesis_id}\n")
        return "handled"
    h = store.get_hypothesis(hid)
    if h is None:
        out.write("  hypothesis not found\n")
        return "handled"
    revised = h.revise(new_claim=ns.claim, new_prediction=ns.prediction)
    store.add_hypothesis(revised)  # new version = new entry (append-only)
    out.write(f"  \u2713 hypothesis revised: {revised.id}  (v{revised.version})\n")
    out.write(f"    revised_from: {h.id} (v{h.version})\n")
    out.write(f"    new claim:    {revised.claim}\n")
    return "handled"


# ──────────────────────────────────────────────────────────────────────────
# 科研工作台 (Argus 吸纳轮): 目录 / use / set / run / report / profile / fav
# — 数据驱动目录 + Metasploit 式控制台流, 输出统一走 render.py 视觉词汇
# (flash 闪讯 / section_header 面板头 / kv_table 信息表 / next_steps 引导面板)。
# 分级纪律: tier 只来自验证器输出, 绝不从输出字符串猜 (Proof Gate 精神)。
# ──────────────────────────────────────────────────────────────────────────

from zall.cli.render import (  # noqa: E402  (工作台段独立 import, 保持既有头部不动)
    flash_err,
    flash_info,
    flash_ok,
    flash_warn,
    kv_table,
    next_steps_panel,
    section_header,
)
from zall.cli.render import _shared_console as _console  # noqa: E402

_NUMERIC_OPTS = frozenset({"residual_bound", "coef_max", "search_bound", "timeout"})
_FALLBACK_STATE: dict[str, Any] = {}


def _st(state: dict[str, Any] | None) -> dict[str, Any]:
    return state if state is not None else _FALLBACK_STATE


def _sci_st() -> Any:
    from zall.extensions.science.state import get_science_state
    return get_science_state()


def _norm_opt(k: str) -> str:
    return k.replace("-", "_").lower()


def _is_numeric(v: str) -> bool:
    try:
        float(v)
        return True
    except ValueError:
        return False


def _mod_by_id(mid: str | None) -> Any:
    if not mid:
        return None
    from zall.extensions.science.catalog import load_catalog
    for m in load_catalog():
        if m.id == str(mid):
            return m
    return None


def _effective_options(sst: Any, mod: Any) -> dict[str, str]:
    """profile 做底 → 全局手动 → 模块级最高 (Argus _merge_options 层级)。

    只保留该模块声明过的选项 (+timeout/out_dir) — profile 的全局预设
    不往无关模块上漏 (dry-run 展示与执行所见即所得)。
    """
    from zall.extensions.science.profiles import apply_profile
    merged = apply_profile(sst.profile, sst.merged_options(mod.id))
    allowed = {_norm_opt(o) for o in mod.options} | {"timeout", "out_dir"}
    return {k: v for k, v in merged.items() if k in allowed}


def _module_pairs(mod: Any, opts: dict[str, str]) -> list[tuple[str, Any]]:
    mode = f"in-process ({mod.certifier})" if mod.is_in_process else f"script ({mod.script})"
    pairs: list[tuple[str, Any]] = [
        ("Section", mod.section),
        ("Mode", mode),
        ("Target Type", mod.primary_input or "None"),
        ("Description", mod.description or "None"),
    ]
    for o in mod.options:
        k = _norm_opt(o)
        val = opts.get(k, "")
        tip = mod.options_help.get(o, "")
        pairs.append((f"opt: {o}", f"{val}  ({tip})" if val and tip else (val or "Not set")))
    return pairs


def _highlighted(mod: Any, opts: dict[str, str]) -> tuple[str, ...]:
    return tuple(f"opt: {o}" for o in mod.options if opts.get(_norm_opt(o)))


_TIER_LEVEL = {"proven": "ok", "corroborated": "info", "refuted": "warn",
               "unknown": "warn", "error": "err"}


def _flash_outcome(out: Any, o: Any) -> None:
    from zall.cli.render import flash
    flash(out, _TIER_LEVEL.get(o.tier, "info"),
          f"{o.module_id}. {o.name} → {o.tier.upper()} ({o.seconds:.2f}s)"
          + (f" — {o.detail}" if o.detail and o.tier in ("error", "unknown") else ""))


# ── modules: 目录浏览 (Argus display_table 对标) ──


def _sci_modules(args: list[str], out: Any) -> str:
    from rich import box

    from zall.extensions.science.catalog import load_catalog
    mods = load_catalog()
    short = "-s" in args
    details = "-d" in args
    show_tags = "-t" in args
    tag_filter = sec_filter = None
    for a in args:
        if a.startswith("-"):
            continue
        if a.startswith("tag:"):
            tag_filter = a[4:].lower()
        else:
            sec_filter = a.lower()
    if tag_filter:
        mods = [m for m in mods if tag_filter in m.tags]
    if sec_filter:
        mods = [m for m in mods
                if sec_filter in m.section.lower() or sec_filter in m.name.lower()]
    if not mods:
        flash_err(out, "no matching research modules")
        return "handled"
    if short:
        section_header(out, f"Research Modules ({len(mods)})")
        for m in mods:
            tags = " ".join(f"[{t}]" for t in sorted(m.tags)) if show_tags else ""
            line = f"{m.id:>4}  {m.name}"
            out.write((line + "  " + tags).rstrip() + "\n")
        return "handled"
    table = Table(
        title=f"Research Modules ({len(mods)})",
        box=box.SIMPLE_HEAVY,
        caption="use <id> · set <opt>=<v> · run <id...> · run --dry-run",
        caption_justify="center",
        expand=False,
    )
    table.add_column("ID", style="yellow", no_wrap=True)
    table.add_column("Name", style="cyan", ratio=1, overflow="fold")
    table.add_column("Section", style="magenta", ratio=1, overflow="fold")
    table.add_column("Mode", no_wrap=True)
    if show_tags:
        table.add_column("Tags", style="magenta", overflow="fold")
    if details:
        table.add_column("Description", ratio=2, overflow="fold")
    for m in mods:
        row = [m.id, m.name, m.section, "in-proc" if m.is_in_process else "script"]
        if show_tags:
            row.append(" ".join(sorted(m.tags)))
        if details:
            row.append(m.description)
        table.add_row(*row)
    console = _console(out)
    console.print()
    console.print(table)
    console.print()
    return "handled"


# ── use: 选中模块 (Argus do_use 对标: id/名字/临时号, 多匹配给 Tmp# 表) ──


def _sci_use(args: list[str], out: Any, state: dict[str, Any] | None) -> str:
    from zall.extensions.science.catalog import find_modules
    st = _st(state)
    sst = _sci_st()
    if not args:
        flash_err(out, "usage: /science use <id|name|tmp#>")
        return "handled"
    ident = " ".join(args).strip()
    last: list[str] = st.get("_sci_last_search") or []
    mod = None
    if ident.isdigit() and last and 1 <= int(ident) <= len(last):
        mod = _mod_by_id(last[int(ident) - 1])
    if mod is None:
        matches = find_modules(ident)
        if not matches:
            flash_err(out, f"no research module matched {ident!r}")
            next_steps_panel(out, ["/science modules — browse the catalog"])
            return "handled"
        if len(matches) > 1:
            section_header(out, "Multiple Matches")
            id_w = max(len(m.id) for m in matches) + 2
            name_w = max(len(m.name) for m in matches) + 2
            out.write(f"  {'Tmp#':<6}{'ID'.ljust(id_w)}{'Name'.ljust(name_w)}Section\n")
            for i, m in enumerate(matches, 1):
                out.write(f"  {str(i):<6}{m.id.ljust(id_w)}{m.name.ljust(name_w)}{m.section}\n")
            out.write("\n")
            flash_info(out, "use '<Tmp#>' or '<ID>' with /science use to select")
            st["_sci_last_search"] = [m.id for m in matches]
            return "handled"
        mod = matches[0]
    st.pop("_sci_last_search", None)
    sst.selected_id = mod.id
    sst.save()
    opts = _effective_options(sst, mod)
    section_header(out, f"Selected: {mod.name} ({mod.id})")
    kv_table(out, f"Module: {mod.name} ({mod.id})", _module_pairs(mod, opts),
             caption="\u21d2 set options, then '/science run'",
             highlight=_highlighted(mod, opts))
    steps = []
    unset_opts = [o for o in mod.options if not opts.get(_norm_opt(o))]
    if unset_opts:
        steps.append(f"/science set {unset_opts[0]}=...")
    steps.append(f"/science run {mod.id} --dry-run")
    steps.append(f"/science run {mod.id}")
    next_steps_panel(out, steps)
    return "handled"


# ── set / unset: 选项 (Argus do_set 弹性语法对标) ──


def _sci_set(args: list[str], out: Any, state: dict[str, Any] | None) -> str:
    import difflib
    sst = _sci_st()
    if not args:
        pairs: list[tuple[str, Any]] = [("profile", sst.profile)]
        pairs += [(k, v) for k, v in sorted(sst.global_options.items())]
        if sst.selected_id:
            mod = _mod_by_id(sst.selected_id)
            for k, v in sorted(sst.module_options.get(sst.selected_id, {}).items()):
                pairs.append((f"{mod.name if mod else sst.selected_id}:{k}", v))
        kv_table(out, "Current Options", pairs or [("None", "")])
        return "handled"
    toks = list(args)
    if len(toks) == 1 and "=" in toks[0]:
        k, v = toks[0].split("=", 1)
        toks = [k, v]
    mid: str | None = None
    if toks[0].isdigit() and len(toks) >= 3 and _mod_by_id(toks[0]) is not None:
        mid = toks[0]
        k = _norm_opt(toks[1])
        v = " ".join(toks[2:])
    else:
        k = _norm_opt(toks[0])
        v = " ".join(toks[1:])
        mid = sst.selected_id if _mod_by_id(sst.selected_id) else None
    if not v.strip():
        flash_err(out, f"option {k!r} needs a value")
        return "handled"
    v = v.strip()
    if k in _NUMERIC_OPTS and not _is_numeric(v):
        flash_err(out, f"option {k!r} expects a numeric value (got {v!r})")
        return "handled"
    mod = _mod_by_id(mid)
    allowed = {_norm_opt(o) for o in mod.options} if mod else set()
    if mod and k not in allowed and k not in ("timeout", "out_dir"):
        sug = difflib.get_close_matches(k, sorted(allowed), n=1, cutoff=0.6)
        hint = f" — did you mean {sug[0]!r}?" if sug else \
            (f" (allowed: {', '.join(sorted(allowed)) or 'none'})" if allowed else "")
        flash_warn(out, f"unknown option {k!r} for {mod.name}{hint}")
        return "handled"
    if mod and k in allowed:
        sst.module_options.setdefault(mod.id, {})[k] = v
        flash_ok(out, f"module {mod.id} | {k} = {v}")
    else:
        sst.global_options[k] = v
        flash_ok(out, f"global option {k} = {v}")
    sst.save()
    return "handled"


def _sci_unset(args: list[str], out: Any, state: dict[str, Any] | None) -> str:
    sst = _sci_st()
    if not args:
        flash_err(out, "usage: /science unset <opt> | <id> <opt>")
        return "handled"
    if len(args) >= 2 and args[0].isdigit():
        mid, k = args[0], _norm_opt(args[1])
        if sst.module_options.get(mid, {}).pop(k, None) is not None:
            flash_ok(out, f"module {mid} option removed: {k}")
        else:
            flash_warn(out, f"that option was not set for module {mid}")
    else:
        k = _norm_opt(" ".join(args))
        if sst.global_options.pop(k, None) is not None:
            flash_ok(out, f"global option removed: {k}")
        elif sst.selected_id and sst.module_options.get(sst.selected_id, {}).pop(k, None) is not None:
            flash_ok(out, f"module {sst.selected_id} option removed: {k}")
        else:
            flash_warn(out, "that option was not set")
    sst.save()
    return "handled"


# ── run: 执行 (Argus do_run 对标: 多 id / --dry-run / 批跑进度) ──


def _resolve_run_ids(tokens: list[str], out: Any) -> list[str] | None:
    from zall.extensions.science.catalog import find_modules
    ids: list[str] = []
    for tok in tokens:
        if tok.isdigit() and _mod_by_id(tok) is not None:
            ids.append(tok)
            continue
        matches = find_modules(tok)
        if len(matches) == 1:
            ids.append(matches[0].id)
        else:
            flash_err(out, f"cannot resolve module {tok!r}"
                           + (" (multiple matches — use an ID)" if matches else ""))
            return None
    return ids


def _sci_run(args: list[str], out: Any, state: dict[str, Any] | None) -> str:
    from zall.extensions.science import runner as sci_runner
    from zall.extensions.science.profiles import PROFILES
    st = _st(state)
    sst = _sci_st()
    dry = "--dry-run" in args
    rest = [a for a in args if a != "--dry-run"]
    timeout = int(PROFILES.get(sst.profile, {}).get("timeout", "900"))
    if "--timeout" in rest:
        i = rest.index("--timeout")
        try:
            timeout = max(5, int(rest[i + 1]))
        except (IndexError, ValueError):
            flash_err(out, "--timeout needs an integer (seconds)")
            return "handled"
        rest = rest[:i] + rest[i + 2:]
    ids = _resolve_run_ids(rest, out) if rest else None
    if ids is None:
        return "handled"
    if not ids:
        if not sst.selected_id:
            flash_err(out, "no module selected")
            next_steps_panel(out, ["/science modules", "/science use <id>"])
            return "handled"
        ids = [sst.selected_id]
    mods = [m for m in (_mod_by_id(i) for i in ids) if m is not None]
    opts_map = {m.id: _effective_options(sst, m) for m in mods}

    if dry:
        section_header(out, f"DRY RUN — {len(mods)} module(s)")
        for m in mods:
            mode = f"in-proc:{m.certifier}" if m.is_in_process else f"script:{m.script}"
            flash_info(out, f"{m.id}. {m.name} [{mode}] timeout={timeout}s")
            opts = opts_map[m.id]
            if opts:
                out.write("      options: "
                          + ", ".join(f"{k}={v}" for k, v in sorted(opts.items())) + "\n")
        return "handled"

    if len(mods) == 1:
        outcomes = [sci_runner.run_module(mods[0], opts_map[mods[0].id],
                                          out=out, timeout=timeout, stream=True)]
    else:
        outcomes = sci_runner.run_batch(mods, opts_map, out=out, timeout=timeout)

    section_header(out, f"Results — {len(outcomes)} run(s), profile '{sst.profile}'")
    for o in outcomes:
        _flash_outcome(out, o)
        if o.claim and o.tier in ("proven", "refuted"):
            out.write(f"      claim: {o.claim}\n")
        if o.detail and o.tier in ("proven", "corroborated", "refuted"):
            out.write(f"      {o.detail}\n")
    st["_sci_last_outcomes"] = outcomes

    # Argus _record_recent 对标: 用满 5 次建议收藏 (用户说了算, 不自动改)
    for o in outcomes:
        if sst.record_use(o.module_id):
            flash_info(out, f"module {o.module_id} used {sst.use_counts[o.module_id]}× — "
                            f"add to favorites? /science fav add {o.module_id}")
    sst.save()

    steps = ["/science report — write REPORT.md + report.json (tier table + chain anchor)"]
    errs = [o for o in outcomes if o.is_error]
    if errs:
        steps.append(f"/science run {errs[0].module_id} --dry-run — inspect options")
    prov = [o for o in outcomes if o.tier in ("proven", "corroborated", "refuted")]
    if prov:
        steps.append("/science auto <topic> — 让 agent 基于此结果继续猜想-反驳循环")
    next_steps_panel(out, steps)
    return "handled"


def _sci_runall(args: list[str], out: Any, state: dict[str, Any] | None) -> str:
    """整组执行 (Argus do_runall 对标): section 名 / tag:x / 无参=全部。"""
    from zall.extensions.science.catalog import load_catalog
    mods = load_catalog()
    if not args:
        ids = [m.id for m in mods]
        label = "all"
    else:
        key = " ".join(args).strip()
        if key.startswith("tag:"):
            tag = key[4:].lower()
            ids = [m.id for m in mods if tag in m.tags]
        else:
            ids = [m.id for m in mods if key.lower() in m.section.lower()]
        label = key
    if not ids:
        flash_err(out, f"no modules match {label!r}")
        next_steps_panel(out, ["/science modules -t — see sections and tags"])
        return "handled"
    flash_info(out, f"runall {label}: {len(ids)} module(s)")
    return _sci_run(ids, out, state)


def _sci_last(out: Any, state: dict[str, Any] | None) -> str:
    """重跑上一次运行集 (Argus do_last 对标)。"""
    st = _st(state)
    outcomes = st.get("_sci_last_outcomes") or []
    ids: list[str] = []
    for o in outcomes:
        mid = getattr(o, "module_id", None)
        if mid and mid not in ids:
            ids.append(mid)
    if not ids:
        flash_warn(out, "nothing has been run yet")
        next_steps_panel(out, ["/science modules", "/science use <id>"])
        return "handled"
    flash_info(out, f"re-running last set: {', '.join(ids)}")
    return _sci_run(ids, out, state)


# ── report: 汇总上一次运行 (Argus report_generator 对标 + 链锚点) ──


def _sci_report(args: list[str], out: Any, state: dict[str, Any] | None) -> str:
    from zall.extensions.science.report import generate_report
    st = _st(state)
    outcomes = st.get("_sci_last_outcomes")
    if not outcomes:
        flash_err(out, "nothing has been run yet")
        next_steps_panel(out, ["/science run <id>", "/science modules"])
        return "handled"
    topic = args[0] if args else "run"
    out_dir = generate_report(outcomes, topic, state=state)
    if out_dir is None:
        flash_err(out, "report generation failed (results dir unwritable?)")
        return "handled"
    flash_ok(out, f"report -> {out_dir / 'REPORT.md'}")
    flash_ok(out, f"machine-readable -> {out_dir / 'report.json'}")
    return "handled"


# ── profile: 研究深度预设 (Argus do_profile 对标) ──


def _sci_profile(args: list[str], out: Any, state: dict[str, Any] | None) -> str:
    from zall.extensions.science.profiles import PROFILES
    sst = _sci_st()
    if not args:
        pairs = [("current", sst.profile)]
        for name, preset in PROFILES.items():
            pairs.append((name, ", ".join(f"{k}={v}" for k, v in sorted(preset.items()))))
        kv_table(out, "Research Profiles", pairs,
                 caption="/science profile <name> to apply")
        return "handled"
    name = args[0].lower()
    if name not in PROFILES:
        flash_err(out, f"unknown profile {name!r} (choose: {', '.join(PROFILES)})")
        return "handled"
    sst.profile = name
    sst.save()
    flash_ok(out, f"profile '{name}' applied "
                  f"({', '.join(f'{k}={v}' for k, v in sorted(PROFILES[name].items()))})")
    flash_info(out, "note: options you set manually always win over profile defaults")
    return "handled"


# ── fav / recent: 收藏与最近 (Argus favorites 对标) ──


def _sci_fav(args: list[str], out: Any, state: dict[str, Any] | None) -> str:
    from zall.extensions.science.catalog import load_catalog
    sst = _sci_st()
    toks = list(args) or ["list"]
    cmd, rest = toks[0], toks[1:]
    mods = {m.id: m for m in load_catalog()}
    if cmd in ("add", "del", "rm", "remove"):
        ids = _resolve_run_ids(rest, out)
        if ids is None:
            return "handled"
        if not ids:
            flash_err(out, f"usage: /science fav {cmd} <id|name>")
            return "handled"
        for mid in ids:
            if cmd == "add":
                sst.favorites.add(mid)
                flash_ok(out, f"added {mid}. {mods[mid].name if mid in mods else mid} to favorites")
            else:
                if mid in sst.favorites:
                    sst.favorites.discard(mid)
                    flash_ok(out, f"removed {mid} from favorites")
                else:
                    flash_warn(out, f"{mid} was not in favorites")
        sst.save()
        return "handled"
    if cmd.startswith("tag:"):
        tag = cmd[4:].lower()
        added = [mid for mid, m in mods.items() if tag in m.tags]
        sst.favorites.update(added)
        sst.save()
        flash_ok(out, f"{len(added)} module(s) tagged '{tag}' added to favorites")
        return "handled"
    if cmd == "clear":
        n = len(sst.favorites)
        sst.favorites.clear()
        sst.save()
        flash_ok(out, f"cleared {n} favorite(s)")
        return "handled"
    if cmd == "run":
        ids = _resolve_run_ids(rest, out) if rest else sorted(sst.favorites, key=lambda x: (len(x), x))
        if not ids:
            flash_warn(out, "favorites list empty")
            next_steps_panel(out, ["/science fav add <id>", "/science fav tag:<tag>"])
            return "handled"
        return _sci_run(list(ids), out, state)
    # list (default)
    if not sst.favorites:
        flash_warn(out, "favorites list empty")
        next_steps_panel(out, ["/science fav add <id>", "/science fav tag:<tag>"])
        return "handled"
    pairs = [(mid, f"{mods[mid].name} · uses={sst.use_counts.get(mid, 0)}"
              if mid in mods else mid)
             for mid in sorted(sst.favorites, key=lambda x: (len(x), x))]
    kv_table(out, f"Favorites ({len(pairs)})", pairs,
             caption="/science fav run · fav tag:<tag> · fav clear")
    return "handled"


def _sci_recent(out: Any, state: dict[str, Any] | None) -> str:
    sst = _sci_st()
    if not sst.recent:
        flash_warn(out, "no recent modules")
        return "handled"
    pairs = []
    for mid in reversed(list(sst.recent)):
        mod = _mod_by_id(mid)
        pairs.append((mid, f"{mod.name if mod else '?'} · used {sst.use_counts.get(mid, 0)}×"
                      + (" · fav" if mid in sst.favorites else "")))
    kv_table(out, f"Recent ({len(pairs)})", pairs, caption="/science run <id> to re-run")
    return "handled"


# ── auto: 自主研究循环 (Phase 3, 混合自主: 规则执行 + LLM 提议) ──


def _sci_auto(args: list[str], out: Any, loop: Any | None,
              state: dict[str, Any] | None) -> str:
    from zall.extensions.science.auto_loop import research_auto
    return research_auto(args, out, loop, state)
