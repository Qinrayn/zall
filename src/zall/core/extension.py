"""zall.core.extension — Extension system (Pi-style self-evolving agent).

Defines the Extension Protocol and ExtensionRegistry that allow third-party
code to hook into the agent's lifecycle without modifying core code.

Lifecycle hooks available:
  on_agent_start(goal, context, messages)   — AgentLoop.run() begins
  on_before_model(messages, step)           — Before each model call
  on_after_tool(tool_id, result, step)      — After each tool execution
  on_user_input(content)                    — User input received (dialog mode)
  on_session_end(egress)                    — AgentLoop.run() ends / finalize()

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


class ExtensionRegistry:
    """Manages extension registration and hook firing.

    Thread-safe for concurrent registration. Hook firing is best-effort:
    a failing extension does not block others (IPR-0).
    """

    def __init__(self) -> None:
        self._extensions: dict[str, Extension] = {}

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

    # ── Hook firing ──

    def fire(self, hook: str, **kwargs: Any) -> None:
        """Fire a lifecycle hook across all registered extensions.

        Iterates all extensions, calling the handler for *hook* if defined.
        Exceptions are caught and logged — one failing extension must not
        block others (IPR-0: presentation layer / extension failures do not
        alter the agent's RunEgress).
        """
        _logger = logging.getLogger(__name__)
        for ext in list(self._extensions.values()):
            handler = ext.hooks.get(hook)
            if handler is None:
                continue
            try:
                handler(**kwargs)
            except (KeyboardInterrupt, SystemExit):
                raise
            except Exception as _exc:
                _logger.warning(
                    "extension '%s' hook '%s' failed: %s",
                    ext.name, hook, _exc,
                )

    @property
    def extension_count(self) -> int:
        return len(self._extensions)