"""zall.core.extension — Extension system (Pi-style self-evolving agent).

Defines the Extension Protocol and ExtensionRegistry that allow third-party
code to hook into the agent's lifecycle without modifying core code.

Lifecycle hooks available:
  Legacy (kwargs-based, backward compatible):
    on_agent_start(goal, context, messages)   — AgentLoop.run() begins
    on_before_model(messages, step)           — Before each model call
    on_after_tool(tool_id, result, step)      — After each tool execution
    on_user_input(content)                    — User input received (dialog mode)
    on_session_end(egress)                    — AgentLoop.run() ends / finalize()

  Typed (new, v0.3.0+):
    Extensions implementing TypedExtension (from lifecycle.py) receive
    typed input models and can return SelfSuggestion lists.

Both systems coexist. New extensions should prefer typed hooks.

IPR constraints:
  IPR-3: stdlib only, no model SDK
  IPR-0: Extension failures must not crash the agent loop
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Protocol, runtime_checkable


# Hook type: each hook receives a dict of keyword arguments
HookHandler = Callable[..., None]


@runtime_checkable
class Extension(Protocol):
    """Pi-style extension protocol.

    An extension is any object with:
      - name: str            — unique identifier
      - hooks: dict          — {hook_name: handler_fn, ...}

    Handler signature: handler(**kwargs) -> None
    Available kwargs depend on the hook point (see module docstring).
    """

    @property
    def name(self) -> str: ...

    @property
    def hooks(self) -> dict[str, HookHandler]: ...


# ── Typed hook detection ──

# Known typed hook methods on TypedExtension (from lifecycle.py)
_TYPED_HOOK_METHODS = frozenset({
    "on_turn_start",
    "on_turn_done",
    "on_tool_result",
    "on_user_input",
})


def _is_typed_extension(ext: Any) -> bool:
    """Check if an extension implements the TypedExtension protocol.

    An extension is "typed" if it has a 'name' property and at least
    one of the typed hook methods (not from hooks dict).
    """
    if not hasattr(ext, "name"):
        return False
    # Check if it has typed hook methods directly (not via hooks dict)
    for method_name in _TYPED_HOOK_METHODS:
        if hasattr(ext, method_name) and callable(getattr(ext, method_name, None)):
            return True
    return False


class ExtensionRegistry:
    """Manages extension registration and hook firing.

    Thread-safe for concurrent registration. Hook firing is best-effort:
    a failing extension does not block others (IPR-0).

    Supports both legacy (kwargs-based) and typed (input-model-based) hooks.
    """

    def __init__(self) -> None:
        self._extensions: dict[str, Extension] = {}
        self._suggestion_accumulator: Any = None  # SuggestionAccumulator, lazy import

    # ── Registration ──

    def register(self, extension: Extension) -> None:
        """Register an extension. Replaces any existing extension with same name."""
        self._extensions[extension.name] = extension

    def unregister(self, name: str) -> None:
        """Remove an extension by name. No-op if not found."""
        self._extensions.pop(name, None)

    def get(self, name: str) -> Extension | None:
        """Look up an extension by name."""
        return self._extensions.get(name)

    def list(self) -> list[Extension]:
        """Return all registered extensions."""
        return list(self._extensions.values())

    def clear(self) -> None:
        """Remove all extensions."""
        self._extensions.clear()

    # ── Legacy hook firing (kwargs-based) ──

    def fire(self, hook: str, **kwargs: Any) -> None:
        """Fire a lifecycle hook across all registered extensions (legacy).

        Iterates all extensions, calling the handler for *hook* if defined
        in the extension's hooks dict. Exceptions are caught and logged.

        For typed extensions, also attempts to call the corresponding typed
        method if one exists (e.g., hook="on_after_tool" → on_tool_result).
        """
        _logger = logging.getLogger(__name__)
        for ext in list(self._extensions.values()):
            # Legacy path: hooks dict
            handler = getattr(ext, "hooks", None)
            if handler is not None and isinstance(handler, dict):
                fn = handler.get(hook)
                if fn is not None:
                    try:
                        fn(**kwargs)
                    except (KeyboardInterrupt, SystemExit):
                        raise
                    except Exception as _exc:
                        _logger.warning(
                            "extension '%s' legacy hook '%s' failed: %s",
                            ext.name, hook, _exc,
                        )

    # ── Typed hook firing (input-model-based, returns suggestions) ──

    def fire_typed(self, hook: str, input_obj: Any) -> list[Any]:
        """Fire a typed lifecycle hook across all registered extensions.

        Unlike fire(), this returns a list of SelfSuggestions accumulated
        from all typed extensions. This is the self-evolution channel.

        Args:
            hook: Typed hook name (e.g. "on_turn_done", "on_tool_result")
            input_obj: Typed input model instance (e.g. TurnDoneInput)

        Returns:
            List of SelfSuggestion objects (may be empty)
        """
        from zall.core.lifecycle import SuggestionAccumulator, SelfSuggestion

        _logger = logging.getLogger(__name__)
        accumulator = SuggestionAccumulator()

        for ext in list(self._extensions.values()):
            if not _is_typed_extension(ext):
                continue

            method = getattr(ext, hook, None)
            if method is None or not callable(method):
                continue

            try:
                result = method(input_obj)
                if result is not None:
                    for item in result:
                        if isinstance(item, SelfSuggestion):
                            accumulator.add(item)
                        else:
                            _logger.warning(
                                "extension '%s' returned non-SelfSuggestion from %s: %r",
                                ext.name, hook, item,
                            )
            except (KeyboardInterrupt, SystemExit):
                raise
            except Exception as _exc:
                _logger.warning(
                    "extension '%s' typed hook '%s' failed: %s",
                    ext.name, hook, _exc,
                )

        return accumulator.suggestions

    def collect_suggestions(self) -> list[Any]:
        """Collect all pending SelfSuggestions from all extensions.

        This is a convenience method that iterates all extensions and
        collects any accumulated suggestions. Returns an empty list
        if no typed extensions have suggestions registered.
        """
        from zall.core.lifecycle import SelfSuggestion, SuggestionAccumulator

        acc = SuggestionAccumulator()
        for ext in list(self._extensions.values()):
            if hasattr(ext, "get_suggestions") and callable(ext.get_suggestions):
                try:
                    suggestions = ext.get_suggestions()
                    if suggestions:
                        for s in suggestions:
                            if isinstance(s, SelfSuggestion):
                                acc.add(s)
                except (KeyboardInterrupt, SystemExit):
                    raise
                except Exception:
                    pass
        return acc.suggestions

    # ── Dual fire: fires both legacy and typed hooks ──

    def fire_all(self, legacy_hook: str, typed_hook: str | None,
                 typed_input: Any | None = None, **kwargs: Any) -> list[Any]:
        """Fire both legacy and typed hooks in one call.

        Args:
            legacy_hook: Name for legacy hooks dict dispatch
            typed_hook: Name for typed method dispatch (None = skip typed)
            typed_input: Typed input model instance (required if typed_hook set)
            **kwargs: Legacy kwargs

        Returns:
            List of SelfSuggestions from typed hooks (may be empty)
        """
        self.fire(legacy_hook, **kwargs)
        if typed_hook is not None and typed_input is not None:
            return self.fire_typed(typed_hook, typed_input)
        return []

    @property
    def extension_count(self) -> int:
        return len(self._extensions)