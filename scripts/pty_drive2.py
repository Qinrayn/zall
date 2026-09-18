"""Drive zall console REPL through a real ConPTY (human-like keystroke test).

v2: reader thread (winpty read() blocks), timestamped action log.
Usage: python scripts/pty_drive2.py <workdir> <outfile> <steps.json>
steps: [{"wait": s} | {"wait_for": "regex", "timeout": s} | {"type": "text"}, ...]
"""
import json
import re
import sys
import threading
import time

from winpty import PtyProcess


def main() -> None:
    workdir, outfile, script_path = sys.argv[1], sys.argv[2], sys.argv[3]
    with open(script_path, encoding="utf-8") as f:
        steps = json.load(f)

    t0 = time.time()

    def say(msg: str) -> None:
        print(f"[{time.time() - t0:6.1f}s] {msg}", flush=True)

    proc = PtyProcess.spawn("python -m zall", cwd=workdir, dimensions=(40, 120))
    say("spawned")
    buf = []
    buf_lock = threading.Lock()
    eof = threading.Event()

    def reader() -> None:
        while not eof.is_set():
            try:
                chunk = proc.read(4096)
            except (EOFError, OSError):
                break
            if not chunk:
                time.sleep(0.03)
                continue
            with buf_lock:
                buf.append(chunk)
        eof.set()

    threading.Thread(target=reader, daemon=True).start()

    def snapshot() -> str:
        with buf_lock:
            return "".join(buf)

    log = open(outfile, "a", encoding="utf-8", errors="replace")

    def pump(duration: float) -> None:
        end = time.time() + duration
        while time.time() < end and not eof.is_set():
            time.sleep(0.05)

    def wait_for(pat: str, timeout: float) -> bool:
        rx = re.compile(pat, re.S)
        end = time.time() + timeout
        while time.time() < end:
            if rx.search(snapshot()):
                return True
            time.sleep(0.05)
        return False

    try:
        for i, step in enumerate(steps):
            if eof.is_set():
                say(f"step {i}: child exited early"); break
            if "wait_for" in step:
                ok = wait_for(step["wait_for"], step.get("timeout", 60))
                say(f"step {i}: wait_for {step['wait_for']!r} -> {'hit' if ok else 'TIMEOUT'}")
                if not ok:
                    log.write(f"\n[[TIMEOUT {step['wait_for']!r}]]\n")
                    break
            if "type" in step:
                say(f"step {i}: typing {step['type'][:40]!r}")
                for line in step["type"].split("\n"):
                    for ch in line:
                        proc.write(ch)
                        time.sleep(0.012)
                    proc.write("\r")
                    time.sleep(0.2)
            if "wait" in step:
                pump(float(step["wait"]))
        pump(4.0)
    finally:
        eof.set()
        alive = proc.isalive()
        if alive:
            proc.terminate(force=True)
        log.write("\n[[driver end]]\n")
        log.write(snapshot())
        log.close()
        say(f"done, child alive={alive}")


if __name__ == "__main__":
    main()
