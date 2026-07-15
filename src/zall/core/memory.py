"""Cross-session memory persistence.

Design:
  - User-authorized memory layer — no implicit cross-run context smuggling
  - Stores user preferences, project knowledge, error patterns, and decisions
  - Injected into system prompt as a "USER MEMORY" section

Memory types:
  1. user_profile: User tech-stack preferences (e.g., "prefers 4-space indent")
  2. project_knowledge: Project-specific knowledge (e.g., "tests use pytest, run with -x")
  3. error_patterns: Common error patterns (e.g., "always forgets to add __init__.py")
  4. decisions: Key architecture decisions (e.g., "uses OpenAI-compatible API")

Storage: ~/.zall/memory.jsonl (JSON Lines, one entry per line)
Injection: Appended to system prompt as "USER MEMORY" section (user-authorized)

IPR constraints:
  IPR-0: Memory loading failure must not block (silent degradation)
  IPR-3: stdlib + json only, no model SDK
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any


# Memory type constants
MEMORY_TYPES = ("user_profile", "project_knowledge", "error_patterns", "decisions")

# Maximum number of memories (prevents unbounded growth)
MAX_MEMORIES = 200


def _memory_path() -> Path:
    """Get the memory file path (~/.zall/memory.jsonl)."""
    home = Path.home()
    if os.name == "nt":
        userprofile = os.environ.get("USERPROFILE", "")
        if userprofile:
            try:
                alt = Path(userprofile)
                if alt.is_dir():
                    home = alt
            except Exception:
                pass
    return home / ".zall" / "memory.jsonl"


class SessionMemory:
    """Cross-session memory manager.

    Usage:
        mem = SessionMemory()
        mem.add("user_profile", "prefers type hints in Python")
        mem.add("project_knowledge", "tests run with pytest -x --tb=short")
        context = mem.build_context()  # inject into system prompt
    """

    __test__ = False

    def __init__(self) -> None:
        self._path = _memory_path()
        self._memories: list[dict[str, Any]] = []
        self._loaded = False

    def _ensure_loaded(self) -> None:
        """Load memories from disk (lazy, loads once)."""
        if self._loaded:
            return
        self._loaded = True
        try:
            if self._path.exists():
                with open(self._path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            entry = json.loads(line)
                            self._memories.append(entry)
                        except (json.JSONDecodeError, KeyError):
                            continue
        except OSError:
            pass  # Silent degradation: memory unavailable does not block

    def add(self, memory_type: str, content: str, *, source: str = "user") -> bool:
        """Add a memory entry.

        Args:
            memory_type: One of user_profile / project_knowledge / error_patterns / decisions
            content: Memory content (concise, one sentence)
            source: Origin ("user" = user-initiated, "agent" = extracted from conversation)

        Returns: True if added successfully
        """
        if memory_type not in MEMORY_TYPES:
            return False
        if not content or not content.strip():
            return False

        self._ensure_loaded()

        # Deduplicate: same type + content is not re-added
        for m in self._memories:
            if m.get("type") == memory_type and m.get("content") == content:
                return True  # Already exists, treat as success

        entry = {
            "type": memory_type,
            "content": content.strip(),
            "source": source,
            "ts": time.time(),
        }
        self._memories.append(entry)

        # Evict oldest entries when exceeding the limit
        if len(self._memories) > MAX_MEMORIES:
            self._memories = self._memories[-MAX_MEMORIES:]

        return self._save()

    def remove(self, content: str) -> bool:
        """Remove a matching memory entry."""
        self._ensure_loaded()
        before = len(self._memories)
        self._memories = [m for m in self._memories if m.get("content") != content]
        if len(self._memories) < before:
            return self._save()
        return False

    def build_context(self) -> str:
        """Build the memory context string for system prompt injection.

        Groups by type, formats output. Returns empty string if no memories.
        """
        self._ensure_loaded()
        if not self._memories:
            return ""

        groups: dict[str, list[str]] = {}
        for m in self._memories:
            groups.setdefault(m["type"], []).append(m["content"])

        labels = {
            "user_profile": "User preferences",
            "project_knowledge": "Project knowledge",
            "error_patterns": "Known error patterns",
            "decisions": "Key decisions",
        }

        lines = ["", "USER MEMORY (cross-session, user-authorized):"]
        for mtype in MEMORY_TYPES:
            items = groups.get(mtype, [])
            if not items:
                continue
            label = labels.get(mtype, mtype)
            lines.append(f"  {label}:")
            for item in items:
                lines.append(f"    - {item}")
        return "\n".join(lines)

    def list_all(self) -> list[dict[str, Any]]:
        """List all memories (for /memory command)."""
        self._ensure_loaded()
        return list(self._memories)

    def clear(self) -> bool:
        """Clear all memories."""
        self._memories.clear()
        try:
            if self._path.exists():
                self._path.unlink()
        except OSError:
            pass
        return True

    def _save(self) -> bool:
        """Persist to disk (JSONL) — B9: 原子write, 崩溃不丢数据。"""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            # 写临时file, 再原子 rename
            import tempfile as _tf
            tmp_path = self._path.parent / f".memory_{_tf._get_default_tempdir().replace('/', '_')}.tmp"
            with open(tmp_path, "w", encoding="utf-8", newline="") as f:
                for m in self._memories:
                    f.write(json.dumps(m, ensure_ascii=False) + "\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(str(tmp_path), str(self._path))
            return True
        except OSError:
            return False
        except Exception:
            # cleanup临时file
            try:
                if tmp_path.exists():
                    tmp_path.unlink()
            except Exception:
                pass
            return False


# Global singleton
_global_memory: SessionMemory | None = None


def get_session_memory() -> SessionMemory:
    """Get the global SessionMemory singleton."""
    global _global_memory
    if _global_memory is None:
        _global_memory = SessionMemory()
    return _global_memory
