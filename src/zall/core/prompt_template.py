"""zall.core.prompt_template — Template rendering system for system prompts.

Inspired by Grok Build's PromptContext + TemplateRenderer pattern.
Provides named templates, variable interpolation, and composition support.

Usage:
    renderer = TemplateRenderer()
    prompt = renderer.render("mid_turn_interjection", text="User said: hello")
    prompt = renderer.render("doom_loop_nudge")

IPR constraints:
  IPR-3: stdlib only, no model SDK
"""

from __future__ import annotations

import re
from typing import Any

# ── Template registry ──

_TEMPLATES: dict[str, str] = {
    # ── Mid-turn interjection (v0.5.0) ──
    "mid_turn_interjection": (
        "[MID-TURN INTERJECTION — The user sent a message "
        "while you were working. Please consider it before "
        "continuing.]\n"
        "${{text}}\n"
        "[/INTERJECTION]"
    ),

    # ── Doom-loop nudge (v0.5.0, inspired by Grok Build) ──
    "doom_loop_nudge": (
        "[SYSTEM: The tool call sequence above was already attempted recently "
        "without progress. Do NOT repeat the same tools. "
        "Try a different approach or analyze what went wrong.]"
    ),

    # ── Empty STOP nudge (v0.0.21) ──
    "empty_stop_nudge": (
        "Your previous turn produced no tool_call and no useful answer. You MUST now emit a "
        "tool_call to actually perform the user''s request (bash / write_file / edit_file / "
        "list_dir / grep / etc.). Do NOT reply with text that only describes what you intend "
        "to do (eg. ''I will create ...'') — that is a failure. Execute the action via a "
        "tool_call in THIS turn. If the request is truly a pure question that needs no tool, "
        "answer it concisely and substantively. Never return an empty response."
    ),

    # ── Session resume notification (v0.4.10) ──
    "session_resume_note": (
        "[resumed from session ${{session_id}}, user explicit]"
    ),
    "session_resume_note_with_update": (
        "[resumed from session ${{session_id}}, user explicit]\n"
        "[UPDATED PROJECT MEMORY - AGENTS.md has changed since this "
        "session was saved]\n"
        "[Current AGENTS.md: ${{agents_md_first_line}}]"
    ),

    # ── Plan mode gate reminder (v0.5.0) ──
    "plan_mode_gate": (
        "[PLAN MODE] Tool '${{tool_id}}' is a write operation. "
        "In plan mode, write operations require explicit confirmation."
    ),

    # ── Perception anomaly nudge (§12.3 E1.1) ──
    "perception_anomaly_nudge": (
        "[perception anomaly: ${{summary}}]"
    ),

    # ── Perception state summary (§12.3 E1.2) ──
    "perception_state_summary": (
        "[perception state: ${{summary}}]"
    ),
}


# ── TemplateRenderer ──


class TemplateRenderer:
    """Simple template renderer with ${{variable}} interpolation.

    Thread-safe: all state is read-only after construction.
    Templates are immutable class-level constants.
    """

    _VAR_PATTERN = re.compile(r"\$\{\{(\w+)\}\}")

    def __init__(self, templates: dict[str, str] | None = None) -> None:
        self._templates = dict(_TEMPLATES)
        if templates:
            self._templates.update(templates)

    def register(self, name: str, template: str) -> None:
        """Register a new template or override an existing one."""
        self._templates[name] = template

    def has(self, name: str) -> bool:
        """Check if a template exists."""
        return name in self._templates

    def get(self, name: str) -> str | None:
        """Get a template by name, or None if not found."""
        return self._templates.get(name)

    def render(self, name: str, **kwargs: Any) -> str:
        """Render a named template with variable substitution.

        Args:
            name: Template name (e.g. "mid_turn_interjection")
            **kwargs: Variable values to substitute

        Returns:
            Rendered string with ${{var}} replaced by kwargs[var]

        Raises:
            KeyError: If template name is not found
        """
        template = self._templates.get(name)
        if template is None:
            raise KeyError(f"Template '{name}' not found")

        return self._render_string(template, **kwargs)

    def render_string(self, template: str, **kwargs: Any) -> str:
        """Render a raw template string with variable substitution.

        Args:
            template: Template string with ${{var}} placeholders
            **kwargs: Variable values to substitute

        Returns:
            Rendered string
        """
        return self._render_string(template, **kwargs)

    def _render_string(self, template: str, **kwargs: Any) -> str:
        """Replace ${{var}} placeholders with kwargs values.

        Unknown variables are left as-is (not replaced).
        """

        def _replacer(m: re.Match) -> str:
            key = m.group(1)
            if key in kwargs:
                value = kwargs[key]
                if isinstance(value, (list, tuple)):
                    return "\n".join(str(v) for v in value)
                return str(value)
            return m.group(0)  # Leave unknown vars as-is

        return self._VAR_PATTERN.sub(_replacer, template)

    @property
    def template_names(self) -> tuple[str, ...]:
        """Return all registered template names."""
        return tuple(self._templates.keys())


# ── Module-level singleton ──

_DEFAULT_RENDERER: TemplateRenderer | None = None


def get_renderer() -> TemplateRenderer:
    """Get or create the default TemplateRenderer singleton."""
    global _DEFAULT_RENDERER
    if _DEFAULT_RENDERER is None:
        _DEFAULT_RENDERER = TemplateRenderer()
    return _DEFAULT_RENDERER


def reset_renderer() -> None:
    """Reset the default renderer (for testing)."""
    global _DEFAULT_RENDERER
    _DEFAULT_RENDERER = None


def render(name: str, **kwargs: Any) -> str:
    """Convenience function: render a named template using the default renderer."""
    return get_renderer().render(name, **kwargs)