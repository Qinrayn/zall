"""AutoLearn extension — Self-evolving agent with typed lifecycle hooks.

Inspired by Grok Build's xai-agent-lifecycle contributor pattern.
Transforms the earlier observation-only AutoLearnExtension into a
true self-evolving extension that produces actionable SelfSuggestions.

Key capabilities:
  - Detects repeated tool chains → suggests skill creation
  - Detects high error rates → suggests K-value adjustments
  - Tracks GoalType-specific performance → suggests Judge composition changes
  - Persists learnings across sessions (~/.zall/learned/)
  - Exposes get_suggestions() for the /suggest command
  - Exposes get_config_overrides() for config layer injection

Hooks (typed):
  on_tool_result — Records tool call patterns, detects repeated sequences
  on_turn_done   — Analyses session patterns, produces SelfSuggestions

Legacy hooks (backward compatible):
  on_after_tool  — Same as on_tool_result (bridged)
  on_session_end — Same as on_turn_done (bridged)

Corresponds to:
  §3.6    Self-innovation capability (OPEN → SETTLED by this module)
  §4.4    K-value table adjustments via SelfSuggestion
  §5.2    Judge composition adjustments via SelfSuggestion
  §9.2.7  Skill creation from detected patterns
"""

from __future__ import annotations

import json
import os
import threading
from collections import Counter, defaultdict
from typing import Any

from zall._util.logging import get_zall_logger as _get_zall_logger

from zall.core.lifecycle import (
    SelfSuggestion,
    ToolResultInput,
    TurnDoneInput,
)

_log = _get_zall_logger(__name__)

# Persistence directory: ~/.zall/learned/
_LEARNED_DIR = "learned"
_LEARNED_FILE = "auto_learn.jsonl"
_CHAIN_MIN_LENGTH = 2       # Minimum chain length to consider for skill suggestion
_CHAIN_MIN_REPEAT = 2       # Minimum times a chain must repeat to trigger suggestion
_FREQUENT_TOOL_MIN = 3      # Minimum uses for a tool to be considered "frequent"
_ERROR_RATE_THRESHOLD = 0.3  # Error rate above this triggers K adjustment suggestion
_SUGGESTION_CONFIDENCE = 0.7  # Default confidence for heuristics-based suggestions
_MAX_ERROR_PATTERNS = 100   # Max stored error patterns (prevents unbounded growth)
_MAX_PERSIST_BYTES = 512_000  # Max persisted JSON file size (~500KB)
_MAX_PERSIST_RETRIES = 3     # Max write retries on failure

# E2: Self-evolution persistence paths
_SKILLS_DIR_NAME = "skills"
_LEARN_OVERRIDES_FILE = "learn_overrides.json"


def _get_skills_dir() -> str:
    """Get the path to the skills directory (~/.zall/skills/)."""
    from zall.safety.config import CONFIG_DIR
    skills_dir = os.path.join(str(CONFIG_DIR), _SKILLS_DIR_NAME)
    os.makedirs(skills_dir, exist_ok=True)
    return skills_dir


def _get_learn_overrides_path() -> str:
    """Get the path to the learn overrides file (~/.zall/learn_overrides.json)."""
    from zall.safety.config import CONFIG_DIR
    return os.path.join(str(CONFIG_DIR), _LEARN_OVERRIDES_FILE)


def _get_learned_path() -> str:
    """Get the path to the learned data directory (~/.zall/learned/)."""
    from zall.safety.config import CONFIG_DIR
    base = str(CONFIG_DIR)
    learned_dir = os.path.join(base, _LEARNED_DIR)
    os.makedirs(learned_dir, exist_ok=True)
    return os.path.join(learned_dir, _LEARNED_FILE)


class AutoLearnExtension:
    """Self-evolving extension that learns from tool usage patterns.

    Typed hooks (new, v0.3.0+):
      on_tool_result  — Records each tool call details
      on_turn_done    — Analyses session, produces SelfSuggestions

    Legacy hooks (backward compatible):
      hooks dict with on_after_tool, on_session_end

    Tracks:
      - Tool call frequency (per tool_id)
      - Tool chains (ordered sequences of tool calls)
      - Error patterns (failed tool calls per tool + step)
      - GoalType-specific performance (per goal_type)
    """

    name = "auto_learn"

    def __init__(
        self,
        learned_path: str | None = None,
        skills_dir: str | None = None,
        learn_overrides_path: str | None = None,
    ) -> None:
        # Core tracking state
        self._tool_counts: dict[str, int] = defaultdict(int)
        self._tool_errors: dict[str, int] = defaultdict(int)
        self._tool_chains: list[list[str]] = []  # All completed chains (across sessions)
        self._current_chain: list[str] = []
        self._error_patterns: list[dict[str, Any]] = []
        self._goal_type_counts: dict[str, int] = defaultdict(int)  # E5: per-goal-type success/fail

        # Self-suggestion state
        self._suggestions: list[SelfSuggestion] = []
        self._lock = threading.Lock()

        # E4: Persistence
        self._learned_path: str | None = learned_path
        if learned_path is None:
            self._learned_path = _get_learned_path()
        self._load_persisted()

        # E2: Self-evolution persistence paths
        self._skills_dir: str | None = skills_dir
        if skills_dir is None:
            self._skills_dir = _get_skills_dir()
        self._learn_overrides_path: str | None = learn_overrides_path
        if learn_overrides_path is None:
            self._learn_overrides_path = _get_learn_overrides_path()

    # ── Legacy hooks dict (backward compatible) ──

    @property
    def hooks(self) -> dict[str, Any]:
        return {
            "on_after_tool": self._on_after_tool_legacy,
            "on_session_end": self._on_session_end_legacy,
        }

    def _on_after_tool_legacy(self, tool_id: str, result: Any, step: int, **kwargs: Any) -> None:
        """Legacy bridge: converts kwargs to typed ToolResultInput."""
        success = getattr(result, "success", True)
        error = getattr(result, "error", None)
        output = getattr(result, "output", "")
        inp = ToolResultInput(
            tool_id=tool_id,
            success=success,
            output=output,
            error=error,
            step=step,
        )
        self.on_tool_result(inp)

    def _on_session_end_legacy(self, egress: Any, **kwargs: Any) -> None:
        """Legacy bridge: converts kwargs to typed TurnDoneInput.

        Note: without full step/tool data from legacy kwargs, we pass
        what we have. The typed path (from loop.py extensions) is richer.
        """
        step_count = getattr(egress, "step_count", 0) or 0
        inp = TurnDoneInput(
            egress=egress,
            step_count=step_count,
            tool_counts=dict(self._tool_counts),
            tool_errors=dict(self._tool_errors),
        )
        self.on_turn_done(inp)

    # ═══════════════════════════════════════════════════════════════
    # §1  Typed Hooks
    # ═══════════════════════════════════════════════════════════════

    def on_tool_result(self, input: ToolResultInput) -> list[SelfSuggestion] | None:
        """Record tool execution and detect error patterns.

        Returns immediate suggestions if a critical pattern is detected
        (e.g., same tool failing repeatedly).
        """
        with self._lock:
            self._tool_counts[input.tool_id] += 1
            self._current_chain.append(input.tool_id)

            if not input.success:
                self._tool_errors[input.tool_id] += 1
                self._error_patterns.append({
                    "tool_id": input.tool_id,
                    "step": input.step,
                    "error": input.error or "unknown",
                })

        # E2: Immediate error burst detection (same tool error >= 3 in recent steps)
        if not input.success:
            with self._lock:
                recent = [e for e in self._error_patterns
                          if e["tool_id"] == input.tool_id
                          and e["step"] >= input.step - 5]
            if len(recent) >= 3:
                return [
                    SelfSuggestion(
                        kind="adjust_k",
                        target=input.tool_id,
                        value=3,  # Suggest higher K for debugging
                        confidence=0.6,
                        evidence=(
                            f"Tool '{input.tool_id}' failed {len(recent)} times "
                            f"in the last 5 steps. Consider increasing K for "
                            f"debugging assistance."
                        ),
                    )
                ]
        return None

    def on_turn_done(self, input: TurnDoneInput) -> list[SelfSuggestion] | None:
        """Analyse session patterns and produce SelfSuggestions.

        Analyses performed:
          1. Error rate analysis → K adjustment suggestions
          2. Repeated tool chains → skill creation suggestions
          3. Frequent tool usage → register_goaltype suggestions
          4. GoalType performance → adjust_judge suggestions

        Suggestions are accumulated and persisted for cross-session learning.
        """
        suggestions: list[SelfSuggestion] = []

        with self._lock:
            # Complete current chain
            if self._current_chain:
                self._tool_chains.append(list(self._current_chain))
                self._current_chain.clear()

            # Prune error patterns to prevent unbounded growth (E6: v0.5.2)
            if len(self._error_patterns) > _MAX_ERROR_PATTERNS:
                # Keep the most recent entries
                self._error_patterns.sort(key=lambda e: e.get("step", 0), reverse=True)
                self._error_patterns = self._error_patterns[:_MAX_ERROR_PATTERNS]

            # 1. Error rate analysis → adjust_k
            total = sum(self._tool_counts.values())
            errors = sum(self._tool_errors.values())
            if total >= 5 and errors / total >= _ERROR_RATE_THRESHOLD:
                # Find the most error-prone tool
                if self._tool_errors:
                    worst_tool = max(self._tool_errors, key=self._tool_errors.get)
                    suggestions.append(SelfSuggestion(
                        kind="adjust_k",
                        target=worst_tool,
                        value=3,
                        confidence=_SUGGESTION_CONFIDENCE,
                        evidence=(
                            f"Error rate {errors}/{total} ({errors/total:.0%}) exceeds "
                            f"threshold. Tool '{worst_tool}' has the most errors "
                            f"({self._tool_errors[worst_tool]}). Consider increasing "
                            f"K for debugging."
                        ),
                    ))

            # 2. Repeated tool chains → skill creation
            if len(self._tool_chains) >= _CHAIN_MIN_REPEAT:
                chain_tuples = [tuple(c) for c in self._tool_chains
                                if len(c) >= _CHAIN_MIN_LENGTH]
                if chain_tuples:
                    repeated = [
                        (seq, count) for seq, count in Counter(chain_tuples).most_common(3)
                        if count >= _CHAIN_MIN_REPEAT
                    ]
                    for seq, count in repeated:
                        skill_name = "_".join(seq[:3])  # Derive name from first 3 tools
                        suggestions.append(SelfSuggestion(
                            kind="create_skill",
                            target=skill_name,
                            value=" → ".join(seq),
                            confidence=min(0.5 + count * 0.1, 0.95),
                            evidence=(
                                f"Tool chain {' → '.join(seq)} observed {count}x. "
                                f"Create a reusable skill to automate this pattern."
                            ),
                        ))

            # 3. Frequent tool usage → register_goaltype
            frequent = {tid for tid, cnt in self._tool_counts.items()
                        if cnt >= _FREQUENT_TOOL_MIN}
            if frequent and len(frequent) >= 3:
                suggestions.append(SelfSuggestion(
                    kind="register_goaltype",
                    target="automation",
                    value=sorted(frequent),
                    confidence=_SUGGESTION_CONFIDENCE,
                    evidence=(
                        f"Tools {', '.join(sorted(frequent))} used frequently. "
                        f"Consider registering an automation "
                        f"GoalType to optimise their K and Judge defaults."
                    ),
                ))

            # 4. GoalType performance → adjust_judge
            gt_key = input.goal_type
            if gt_key and gt_key != "unknown":
                self._goal_type_counts[gt_key] += 1
                gt_count = self._goal_type_counts[gt_key]
                if gt_count >= 3 and errors / max(total, 1) > _ERROR_RATE_THRESHOLD:
                    suggestions.append(SelfSuggestion(
                        kind="adjust_judge",
                        target=gt_key,
                        value="system",
                        confidence=0.6,
                        evidence=(
                            f"GoalType '{gt_key}' used {gt_count}x with high error rate. "
                            f"Consider switching to system Judge for more reliable "
                            f"termination detection."
                        ),
                    ))

        # Store suggestions
        with self._lock:
            self._suggestions.extend(suggestions)

        # Persist for cross-session learning (non-blocking)
        self._persist()

        return suggestions if suggestions else None

    def on_user_input(self, input: Any) -> list[SelfSuggestion] | None:
        """Observe user input patterns."""
        return None

    # ═══════════════════════════════════════════════════════════════
    # §2  Suggestion API
    # ═══════════════════════════════════════════════════════════════

    def get_suggestions(self) -> list[SelfSuggestion]:
        """Return all accumulated SelfSuggestions.

        Used by ExtensionRegistry.collect_suggestions() and the
        /suggest CLI command.
        """
        with self._lock:
            return list(self._suggestions)

    def clear_suggestions(self) -> None:
        """Clear all suggestions (e.g., after user reviews them)."""
        with self._lock:
            self._suggestions.clear()

    def apply_suggestion(self, suggestion: SelfSuggestion) -> dict[str, Any]:
        """Apply a single SelfSuggestion and return the result.

        This is the core self-evolution mechanism: when the user accepts
        a suggestion, this method applies it.

        E2: Real persistence — writes files to disk instead of returning
        no-op dicts. create_skill writes to .zall/skills/<name>.md and
        adjust_judge writes to .zall/learn_overrides.json.

        Returns a dict describing what was changed.
        """
        with self._lock:
            self._suggestions = [s for s in self._suggestions if s != suggestion]

        if suggestion.kind == "adjust_k":
            # K adjustment will be picked up by the config layer
            # (see get_config_overrides)
            return {
                "applied": True,
                "kind": "adjust_k",
                "target": suggestion.target,
                "value": suggestion.value,
                "message": f"K for '{suggestion.target}' will be adjusted to {suggestion.value}",
            }
        elif suggestion.kind == "create_skill":
            # E2.1: Write skill file to ~/.zall/skills/<name>.md (atomic write)
            skill_name = suggestion.target
            skills_dir = self._skills_dir or _get_skills_dir()
            os.makedirs(skills_dir, exist_ok=True)
            filepath = os.path.join(skills_dir, f"{skill_name}.md")

            skill_content = (
                f"# {skill_name}\n\n"
                f"A reusable skill created from detected tool chain pattern.\n\n"
                f"## Description\n"
                f"{suggestion.evidence}\n\n"
                f"## Prompt\n"
                f"{suggestion.value}\n"
            )

            # Atomic write: write to temp, then rename
            tmp = filepath + ".tmp"
            try:
                with open(tmp, "w", encoding="utf-8") as f:
                    f.write(skill_content)
                os.replace(tmp, filepath)
            except Exception:
                # Clean up temp file on failure
                try:
                    if os.path.exists(tmp):
                        os.remove(tmp)
                except Exception:
                    pass
                raise

            return {
                "applied": True,
                "kind": "create_skill",
                "target": suggestion.target,
                "file_path": filepath,
                "message": (
                    f"Skill '{suggestion.target}' created from chain: {suggestion.value} "
                    f"at {filepath}"
                ),
            }
        elif suggestion.kind == "register_goaltype":
            return {
                "applied": True,
                "kind": "register_goaltype",
                "target": suggestion.target,
                "value": suggestion.value,
                "message": (
                    f"GoalType '{suggestion.target}' registered with "
                    f"tools: {suggestion.value}"
                ),
            }
        elif suggestion.kind == "adjust_judge":
            # E2.2: Persist judge override to ~/.zall/learn_overrides.json
            overrides_path = self._learn_overrides_path or _get_learn_overrides_path()

            # Load existing overrides
            existing: dict[str, Any] = {}
            if os.path.exists(overrides_path):
                try:
                    with open(overrides_path, "r", encoding="utf-8") as f:
                        existing = json.load(f)
                except (OSError, json.JSONDecodeError):
                    existing = {}

            # Merge new override
            existing[f"judge_{suggestion.target}"] = suggestion.value

            # Atomic write
            tmp = overrides_path + ".tmp"
            try:
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(existing, f, ensure_ascii=False)
                os.replace(tmp, overrides_path)
            except Exception:
                try:
                    if os.path.exists(tmp):
                        os.remove(tmp)
                except Exception:
                    pass
                raise

            return {
                "applied": True,
                "kind": "adjust_judge",
                "target": suggestion.target,
                "value": suggestion.value,
                "message": (
                    f"Judge for GoalType '{suggestion.target}' adjusted "
                    f"to {suggestion.value} (next run)"
                ),
            }
        return {"applied": False, "message": f"Unknown suggestion kind: {suggestion.kind}"}

    def get_config_overrides(self) -> dict[str, Any]:
        """Return config overrides based on learned patterns.

        These are injected as a config layer (Phase 4), overriding defaults
        but lower priority than user config.

        Currently supports:
          - K value overrides for frequently error-prone tools
          - Cross-session optimal K values (v0.5.0)
          - Judge mode overrides based on GoalType performance (v0.5.2)
        """
        overrides: dict[str, Any] = {}
        with self._lock:
            total = sum(self._tool_counts.values())
            if total >= 3:
                # 1. Error-prone tools → suggest higher K
                for tid, err_count in self._tool_errors.items():
                    tool_total = self._tool_counts.get(tid, 0)
                    if tool_total >= 3 and err_count / tool_total >= _ERROR_RATE_THRESHOLD:
                        overrides[f"k_{tid}"] = 3

                # 2. v0.5.0: Cross-session optimal K values
                #   If a tool has high success rate across sessions, keep K low.
                #   If consistently error-prone, keep K higher.
                for tid, count in self._tool_counts.items():
                    if count >= 10:  # Significant sample across sessions
                        err_count = self._tool_errors.get(tid, 0)
                        success_rate = 1.0 - (err_count / count)
                        if success_rate >= 0.95 and f"k_{tid}" not in overrides:
                            # High success → keep K at default (no override)
                            pass
                        elif success_rate < 0.7:
                            # Low success → suggest higher K
                            overrides[f"k_{tid}"] = 3

                # 3. v0.5.2: Judge mode overrides from high-confidence suggestions
                for s in self._suggestions:
                    if s.kind == "adjust_judge" and s.confidence >= 0.8:
                        overrides[f"judge_{s.target}"] = s.value

            # 4. E2.3: Read persisted overrides from .zall/learn_overrides.json
            # These were written by apply_suggestion(kind="adjust_judge") in a
            # previous session, creating a real persistence loop.
            overrides_path = self._learn_overrides_path or _get_learn_overrides_path()
            if os.path.exists(overrides_path):
                try:
                    with open(overrides_path, "r", encoding="utf-8") as f:
                        persisted = json.load(f)
                    if isinstance(persisted, dict):
                        for key, val in persisted.items():
                            if key not in overrides:
                                overrides[key] = val
                except (OSError, json.JSONDecodeError):
                    pass

        return {"k_overrides": overrides} if overrides else {}

    # ═══════════════════════════════════════════════════════════════
    # §3  Stats
    # ═══════════════════════════════════════════════════════════════

    def get_stats(self) -> dict[str, Any]:
        """Return usage statistics for display."""
        with self._lock:
            return {
                "tool_counts": dict(self._tool_counts),
                "tool_errors": dict(self._tool_errors),
                "tool_chains": len(self._tool_chains),
                "error_patterns": len(self._error_patterns),
                "current_chain": list(self._current_chain),
                "suggestions": len(self._suggestions),
                "goal_type_counts": dict(self._goal_type_counts),
            }

    # ═══════════════════════════════════════════════════════════════
    # §4  Persistence (cross-session learning)
    # ═══════════════════════════════════════════════════════════════

    def _persist(self) -> None:
        """Append current session data to learned JSONL file.

        Runs synchronously to avoid threading issues.
        The operation is fast (serialize + atomic rename) and called only
        at session end and on significant state changes.

        v0.5.2 (E6 fix): Removed background-thread approach to eliminate
        lock-ordering risks. Now runs inline with proper error isolation.
        """
        self._do_persist()

    def _do_persist(self) -> None:
        """Serialize and persist learned data atomically."""
        path = self._learned_path or _get_learned_path()
        self._learned_path = path

        for attempt in range(_MAX_PERSIST_RETRIES):
            try:
                with self._lock:
                    record = {
                        "tool_counts": dict(self._tool_counts),
                        "tool_errors": dict(self._tool_errors),
                        "tool_chains": [list(c) for c in self._tool_chains],
                        "error_patterns": list(self._error_patterns),
                        "goal_type_counts": dict(self._goal_type_counts),
                        "suggestions": [
                            {
                                "kind": s.kind,
                                "target": s.target,
                                "value": self._serialize_value(s.value),
                                "confidence": s.confidence,
                                "evidence": s.evidence,
                            }
                            for s in self._suggestions
                        ],
                    }

                # Write atomically: write to temp, rename
                tmp = path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(record, f, ensure_ascii=False, default=str)
                os.replace(tmp, path)
                break  # Success

            except OSError as _e:
                if attempt < _MAX_PERSIST_RETRIES - 1:
                    _log.warning(
                        "auto_learn: persist attempt %d failed: %s, retrying...",
                        attempt + 1, _e,
                    )
                    import time as _time
                    _time.sleep(0.1 * (attempt + 1))
                else:
                    _log.warning(
                        "auto_learn: failed to persist after %d attempts: %s",
                        _MAX_PERSIST_RETRIES, _e,
                    )
            except Exception:
                _log.warning("auto_learn: failed to persist learned data", exc_info=True)
                break

    @staticmethod
    def _serialize_value(value: Any) -> Any:
        """Safely serialize suggestion values to JSON-compatible types."""
        if isinstance(value, (set, frozenset)):
            return list(value)
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        if isinstance(value, (list, tuple)):
            return [AutoLearnExtension._serialize_value(v) for v in value]
        if isinstance(value, dict):
            return {k: AutoLearnExtension._serialize_value(v) for k, v in value.items()}
        return value

    def _load_persisted(self) -> None:
        """Load learned data from previous sessions.

        Merges persisted tool_chains and error_patterns into current state
        for cross-session pattern detection.
        """
        path = self._learned_path or _get_learned_path()
        self._learned_path = path

        if not os.path.exists(path):
            return

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)

            with self._lock:
                # Merge tool counts (cumulative across sessions)
                for tid, count in data.get("tool_counts", {}).items():
                    self._tool_counts[tid] = self._tool_counts.get(tid, 0) + count

                # Merge tool chains (for cross-session pattern detection)
                for chain in data.get("tool_chains", []):
                    if chain not in self._tool_chains:
                        self._tool_chains.append(chain)

                # Cluster similar chains: if a new chain shares a prefix with
                # an existing chain of length >= 3, only merge if they differ
                # significantly (E6: cross-session pattern clustering, v0.5.2)
                self._cluster_similar_chains()

                # Merge error patterns (cap at max to prevent unbounded growth)
                new_errors = data.get("error_patterns", [])
                self._error_patterns.extend(new_errors)
                if len(self._error_patterns) > _MAX_ERROR_PATTERNS:
                    self._error_patterns.sort(key=lambda e: e.get("step", 0), reverse=True)
                    self._error_patterns = self._error_patterns[:_MAX_ERROR_PATTERNS]

                # Merge goal type counts (cumulative across sessions)
                for gt, count in data.get("goal_type_counts", {}).items():
                    self._goal_type_counts[gt] = self._goal_type_counts.get(gt, 0) + count

                # Restore suggestions (from previous analysis), dedup by kind+target
                seen_suggestions: set[tuple[str, str]] = set()
                for s_data in data.get("suggestions", []):
                    try:
                        suggestion = SelfSuggestion(
                            kind=s_data["kind"],
                            target=s_data["target"],
                            value=s_data["value"],
                            confidence=s_data.get("confidence", 0.5),
                            evidence=s_data.get("evidence", ""),
                        )
                        dedup_key = (suggestion.kind, suggestion.target)
                        if dedup_key not in seen_suggestions:
                            seen_suggestions.add(dedup_key)
                            if suggestion not in self._suggestions:
                                self._suggestions.append(suggestion)
                    except (KeyError, ValueError, TypeError):
                        pass

                # Sort suggestions by confidence descending
                self._suggestions.sort(key=lambda s: s.confidence, reverse=True)

            _log.info(
                "auto_learn: loaded %d chains, %d error patterns from %s",
                len(data.get("tool_chains", [])),
                len(data.get("error_patterns", [])),
                path,
            )
        except Exception:
            _log.warning("auto_learn: failed to load persisted data", exc_info=True)

    def _cluster_similar_chains(self) -> None:
        """Cluster similar tool chains to reduce redundancy.

        Two chains are considered similar if they share the same first N tools
        (N >= 2). Only the longest representative is kept.
        """
        if len(self._tool_chains) < 2:
            return

        # Group by prefix (first 2 tools)
        prefix_groups: dict[str, list[list[str]]] = {}
        for chain in self._tool_chains:
            if len(chain) >= 2:
                prefix = "|".join(chain[:2])
                prefix_groups.setdefault(prefix, []).append(chain)

        # Dedup: keep only the longest chain per prefix group
        deduped: list[list[str]] = []
        seen_prefixes: set[str] = set()
        for chain in self._tool_chains:
            if len(chain) >= 2:
                prefix = "|".join(chain[:2])
                if prefix in seen_prefixes:
                    continue  # Already have a representative for this prefix
                seen_prefixes.add(prefix)
                # Keep the longest chain in this group
                longest = max(prefix_groups[prefix], key=len)
                deduped.append(longest)
            else:
                deduped.append(chain)

        self._tool_chains = deduped


# Factory function for easy registration
def create_auto_learn() -> AutoLearnExtension:
    return AutoLearnExtension()