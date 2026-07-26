"""Convert the falsifiability-gated discovery paper.md to a compilable paper.tex."""
import re
from pathlib import Path

SRC = Path("docs/paper/FALSIFIABILITY_GATED_DISCOVERY.md")
OUT = Path(r"C:\Users\云丘\Desktop\投稿文件2\paper.tex")

md = SRC.read_text(encoding="utf-8")

# ── LaTeX preamble ──
preamble = r"""\documentclass[11pt,a4paper]{article}
\usepackage[utf8]{inputenc}
\usepackage[T1]{fontenc}
\usepackage[margin=1in]{geometry}
\usepackage{amsmath,amssymb}
\usepackage{hyperref}
\usepackage{booktabs}
\usepackage{listings}
\usepackage{enumitem}
\usepackage{xcolor}

\lstset{
  basicstyle=\small\ttfamily,
  breaklines=true,
  frame=single,
  columns=fullflexible,
}

\title{Falsifiability-Gated Discovery:\\Machine-Checkable Epistemic Tiers for Self-Improving Agents}
\author{Yuhan Zhang\\\\\texttt{qinray@hotmail.com}\\\\\href{https://orcid.org/0009-0000-2769-467X}{ORCID: 0009-0000-2769-467X}}
\date{July 2026}

\begin{document}
\maketitle
"""

# ── Parse and convert ──
lines = md.split("\n")
tex_lines: list[str] = []
in_abstract = False
in_code = False
in_table = False
table_buf: list[str] = []
skip_header = True  # skip the md title/status lines

def escape(s: str) -> str:
    """Escape LaTeX special chars and replace Unicode math symbols."""
    s = s.replace("&", r"\&")
    s = s.replace("%", r"\%")
    s = s.replace("#", r"\#")
    # Unicode math → LaTeX commands
    s = s.replace("∀", r"$\forall$")
    s = s.replace("≡", r"$\equiv$")
    s = s.replace("≈", r"$\approx$")
    s = s.replace("≥", r"$\geq$")
    s = s.replace("≤", r"$\leq$")
    s = s.replace("×", r"$\times$")
    s = s.replace("∈", r"$\in$")
    s = s.replace("∉", r"$\notin$")
    s = s.replace("∪", r"$\cup$")
    s = s.replace("ℂ", r"$\mathbb{C}$")
    s = s.replace("ℚ", r"$\mathbb{Q}$")
    s = s.replace("ℤ", r"$\mathbb{Z}$")
    s = s.replace("→", r"$\rightarrow$")
    s = s.replace("—", "---")
    s = s.replace("–", "--")
    s = s.replace("…", "...")
    s = s.replace("“", "``")
    s = s.replace("”", "''")
    s = s.replace("’", "'")
    return s

def inline_fmt(s: str) -> str:
    """Convert inline markdown: **bold**, *italic*, `code`. Also escape."""
    s = escape(s)
    # Code first (to protect from bold/italic processing)
    s = re.sub(r'`([^`]+)`', r'\\texttt{\1}', s)
    # Bold
    s = re.sub(r'\*\*([^*]+)\*\*', r'\\textbf{\1}', s)
    # Italic
    s = re.sub(r'\*([^*]+)\*', r'\\textit{\1}', s)
    return s

def flush_table():
    global table_buf, in_table
    if not table_buf:
        return
    # Determine columns
    cols = max(len(row) for row in table_buf)
    tex_lines.append(r"\begin{table}[h]\centering")
    tex_lines.append(r"\begin{tabular}{" + "l" * cols + "}")
    tex_lines.append(r"\toprule")
    for i, row in enumerate(table_buf):
        cells = [inline_fmt(c.strip()) for c in row]
        while len(cells) < cols:
            cells.append("")
        tex_lines.append(" & ".join(cells) + r" \\")
        if i == 0:
            tex_lines.append(r"\midrule")
    tex_lines.append(r"\bottomrule")
    tex_lines.append(r"\end{tabular}")
    tex_lines.append(r"\end{table}")
    tex_lines.append("")
    table_buf = []
    in_table = False

i = 0
while i < len(lines):
    line = lines[i]

    # Skip the md title line and status line
    if skip_header and (line.startswith("# ") or line.startswith("**Draft")):
        i += 1
        continue
    if skip_header and line.strip() == "---":
        skip_header = False
        i += 1
        continue

    # Abstract detection
    if line.strip() == "## Abstract":
        tex_lines.append(r"\begin{abstract}")
        in_abstract = True
        i += 1
        continue
    if in_abstract and line.startswith("## "):
        tex_lines.append(r"\end{abstract}")
        tex_lines.append("")
        in_abstract = False
        # fall through to process this heading

    # Section headings
    if line.startswith("### "):
        flush_table()
        title = inline_fmt(re.sub(r'^[\d.]+\s*', '', line[4:].strip()))
        tex_lines.append(f"\\subsection{{{title}}}")
        i += 1
        continue
    if line.startswith("## "):
        flush_table()
        title = inline_fmt(re.sub(r'^[\d.]+\s*', '', line[3:].strip()))
        tex_lines.append(f"\\section{{{title}}}")
        i += 1
        continue

    # Horizontal rules → skip
    if line.strip() == "---":
        flush_table()
        i += 1
        continue

    # Code blocks
    if line.startswith("```"):
        flush_table()
        if not in_code:
            tex_lines.append(r"\begin{lstlisting}")
            in_code = True
        else:
            tex_lines.append(r"\end{lstlisting}")
            in_code = False
        i += 1
        continue
    if in_code:
        tex_lines.append(line)
        i += 1
        continue

    # Tables
    if line.startswith("|"):
        cells = [c.strip() for c in line.split("|")[1:-1]]
        # Skip separator rows (---|---|---)
        if cells and all(set(c) <= set("-: ") for c in cells):
            i += 1
            continue
        if cells:
            in_table = True
            table_buf.append(cells)
        i += 1
        continue
    else:
        if in_table:
            flush_table()

    # Bullet lists
    if line.startswith("- "):
        content = inline_fmt(line[2:].strip())
        # Check if previous was also a bullet (continuing list)
        if tex_lines and not tex_lines[-1].startswith(r"\item") and not tex_lines[-1] == r"\begin{itemize}":
            tex_lines.append(r"\begin{itemize}[leftmargin=*]")
        tex_lines.append(f"  \\item {content}")
        # Look ahead: if next line is not a bullet, close
        if i + 1 < len(lines) and not lines[i + 1].startswith("- "):
            tex_lines.append(r"\end{itemize}")
        i += 1
        continue

    # Empty line → paragraph break
    if not line.strip():
        flush_table()
        tex_lines.append("")
        i += 1
        continue

    # Normal paragraph text
    tex_lines.append(inline_fmt(line))
    i += 1

flush_table()
if in_abstract:
    tex_lines.append(r"\end{abstract}")

# Close document
tex_lines.append("")
tex_lines.append(r"\end{document}")

# Post-process: fix remaining multi-line markdown bold/italic
full = preamble + "\n".join(tex_lines)
# Fix **...** spanning (greedy within ~200 chars)
full = re.sub(r'\*\*(.+?)\*\*', r'\\textbf{\1}', full)
# Fix *...* (non-greedy, avoid matching already-escaped \*)
full = re.sub(r'(?<!\\)\*([^*\n]+?)\*', r'\\textit{\1}', full)

# Write
OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(full, encoding="utf-8")
print(f"OK: {OUT.name} ({len(full):,} chars, {full.count(chr(10))} lines)")
