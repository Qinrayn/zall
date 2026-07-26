"""zall.cli.commands.science - Science Kit CLI commands (E3).

Corresponds to:
  MASTER.md §12.3 E3 (Science Kit 首个闭环)
  docs/E3_SCIENCE_KIT.md §3 (CLI)

Commands:
  /science new "claim" --prediction "..."   create a hypothesis
  /science list                              list all hypotheses
  /science show <id>                         show hypothesis + evidence
  /science evidence <hid> --metric M --value V [--threshold T] --supports/--against
  /science falsify <hid> --evidence <eid> --diagnosis "..." --value "..."
  /science revise <hid> --claim "..." --prediction "..."

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


@slash_command("/science", description="science kit: hypotheses, evidence, falsification", category=_CATEGORY_SCIENCE)
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
    else:
        out.write("  usage: /science new|list|show|evidence|falsify|revise\n")
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
