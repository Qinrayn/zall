"""Strip ANSI escapes from a ConPTY transcript for human reading."""
import re
import sys

raw = open(sys.argv[1], encoding="utf-8", errors="replace").read()
clean = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", raw)
clean = re.sub(r"\x1b\][^\x07\x1b]*(\x07|\x1b\\)", "", clean)
clean = clean.replace("\r\n", "\n").replace("\r", "\n")
clean = re.sub(r"[\x00-\x08\x0b-\x1f]", "", clean)
lines = [l.rstrip() for l in clean.split("\n")]
out, blank = [], 0
for l in lines:
    if l.strip():
        out.append(l)
        blank = 0
    else:
        blank += 1
        if blank <= 1:
            out.append("")
print("\n".join(out))
