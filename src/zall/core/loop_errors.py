"""zall.core.loop_errors — Agent loop exception types."""

class ToolNotFound(Exception):
    """The model invoked a tool_id not registered in ToolRegistry."""

class AgentRunaway(Exception):
    """Agent exceeded MAX_STEPS without terminating."""

class ContextLimitExceeded(Exception):
    """Context window exceeded (LENGTH stop_reason)."""
