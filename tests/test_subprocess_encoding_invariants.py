"""Invariant: subprocess text-mode calls must pin encoding (GBK Windows).

Corresponds to CHANGELOG "外部评估修复轮 — GBK 修复 (中文 Windows)" and the
2026-07-26 bugfix round: `text=True` without an explicit `encoding=` decodes
child output with the console codepage (GBK on zh-CN Windows), while git /
pytest / plugins emit UTF-8 — this crashed subprocess reader threads with
UnicodeDecodeError (observed via PytestUnhandledThreadExceptionWarning in the
full suite run).

Invariant (I-GBK): every `text=True` occurrence in src/zall must live inside
a call expression that also passes `encoding=`.

Counterexample: the scanner itself is exercised against a synthetic bad
snippet — if the scanner cannot flag a known-bad call, the test fails (the
guard would be decorative, IPR-0).
"""
from __future__ import annotations

import pathlib
import re

SRC = pathlib.Path(__file__).resolve().parent.parent / "src" / "zall"


def _call_span(src: str, idx: int) -> tuple[int, int] | None:
    """Span of the call parens enclosing position idx (balanced scan)."""
    depth = 0
    start = -1
    for i in range(idx, -1, -1):
        c = src[i]
        if c == ")":
            depth += 1
        elif c == "(":
            if depth == 0:
                start = i
                break
            depth -= 1
    if start < 0:
        return None
    depth = 0
    for j in range(start, len(src)):
        c = src[j]
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return (start, j + 1)
    return None


def _violations(source: str) -> list[int]:
    """Return character offsets of text=True calls lacking encoding=."""
    found: list[int] = []
    for m in re.finditer(r"text=True", source):
        span = _call_span(source, m.start())
        if span is None:
            continue
        if "encoding" not in source[span[0]:span[1]]:
            found.append(m.start())
    return found


def test_no_text_true_without_encoding() -> None:
    """I-GBK: no subprocess text=True call in src/zall may omit encoding."""
    offenders: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        for off in _violations(source):
            line = source.count("\n", 0, off) + 1
            offenders.append(f"{path.relative_to(SRC.parent)}:{line}")
    assert not offenders, (
        "text=True without encoding= (GBK Windows decode crash risk); "
        'add encoding="utf-8", errors="replace": ' + ", ".join(offenders)
    )


def test_scanner_flags_known_bad_call() -> None:
    """Counterexample: scanner must flag a bad call, else the guard is dead."""
    bad = 'r = subprocess.run(["git", "diff"], capture_output=True, text=True, timeout=5)'
    assert _violations(bad), "scanner failed to flag a known-bad call"


def test_scanner_accepts_fixed_call() -> None:
    """Positive twin: the repaired form must not be flagged."""
    good = (
        'r = subprocess.run(["git", "diff"], capture_output=True, text=True,'
        ' encoding="utf-8", errors="replace", timeout=5)'
    )
    assert not _violations(good)
