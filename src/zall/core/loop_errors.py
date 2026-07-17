"""zall.core.loop_errors — Agent loop exception types."""

import warnings as _warnings
from typing import Any


class ToolNotFound(Exception):
    """The model invoked a tool_id not registered in ToolRegistry."""

class AgentRunaway(Exception):
    """Agent exceeded MAX_STEPS without terminating."""

# ContextLimitExceeded was defined here in v0.x but is no longer used
# (LENGTH stop_reason handling is internal to AgentLoop, not exception-based).
# Kept as an alias for backward compatibility only — do NOT raise or catch.
# Kept as an alias for backward compatibility only — do NOT raise or catch.

class ContextLimitExceeded(Exception):
    """Deprecated: kept for backward compatibility. Do not use."""
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        _warnings.warn(
            "ContextLimitExceeded is deprecated and unused. "
            "LENGTH handling is internal to AgentLoop.",
            DeprecationWarning, stacklevel=2,
        )
        super().__init__(*args, **kwargs)
