"""zall._util.logging — Unified logging configuration (IPR-0: self-falsifiable errors).

Inspired by Grok Build's xai-tracing crate: structured, contextual logging that
makes non-fatal errors observable without changing control flow.

Design:
  - Single entry point: get_zall_logger(__name__) → standard library logger
  - ZALL_LOG_LEVEL env var controls verbosity (default: WARNING)
  - IPR-0 invariant: observer/renderer errors are swallowed but LOGGED
    (silent pass → violates falsifiability; logged pass → observable)

Usage:
    from zall._util.logging import get_zall_logger
    logger = get_zall_logger(__name__)
    ...
    except Exception as e:
        logger.warning("operation failed: %s", e)  # observable, non-fatal

Corresponds to:
  §0     PR-0: self-falsifiability — silent errors are unfalsifiable
  IPR-0  Self-falsifiability code form — errors must be observable
"""

from __future__ import annotations

import logging
import os
import sys
from typing import TextIO

# ── Defaults ──

_DEFAULT_LEVEL = logging.WARNING
_ENV_VAR = "ZALL_LOG_LEVEL"

# Level name → int mapping for env var parsing
_LEVEL_NAMES: dict[str, int] = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}


def _resolve_level() -> int:
    """Resolve log level from environment or default."""
    raw = os.environ.get(_ENV_VAR, "").strip().upper()
    if raw in _LEVEL_NAMES:
        return _LEVEL_NAMES[raw]
    if raw and raw.isdigit():
        return int(raw)
    return _DEFAULT_LEVEL


# ── Formatter ──

_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_FORMAT_DATE = "%H:%M:%S"


def _make_handler(stream: TextIO = sys.stderr) -> logging.Handler:
    """Create a stderr handler with the standard format."""
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter(_FORMAT, _FORMAT_DATE))
    return handler


# ── Global state (lazy-initialized) ──

_initialized = False


def setup_logging(
    *,
    level: int | None = None,
    stream: TextIO | None = None,
    force: bool = False,
) -> None:
    """Configure the root zall logger.

    Called once at process start (from main/REPL). Safe to call multiple
    times — subsequent calls are no-ops unless *force=True*.

    Args:
        level: Log level (default: from ZALL_LOG_LEVEL env, or WARNING).
        stream: Output stream (default: sys.stderr).
        force: Reconfigure even if already initialized.
    """
    global _initialized
    if _initialized and not force:
        return

    root = logging.getLogger("zall")
    root.setLevel(level if level is not None else _resolve_level())

    # Remove existing handlers to avoid duplicates on force re-init
    if force:
        root.handlers.clear()

    if not root.handlers:
        root.addHandler(_make_handler(stream or sys.stderr))

    _initialized = True


def get_zall_logger(name: str) -> logging.Logger:
    """Get a zall-namespaced logger.

    Usage:
        logger = get_zall_logger(__name__)
        logger.warning("non-fatal issue: %s", detail)

    The logger name is prefixed with 'zall.' to keep it under the
    zall root logger's level configuration.
    """
    # Strip leading 'zall.' if already present to avoid 'zall.zall...'
    if name.startswith("zall."):
        logger_name = name
    elif name.startswith("zall"):
        logger_name = f"zall.{name}"
    else:
        logger_name = f"zall.{name}"

    return logging.getLogger(logger_name)


def get_log_level() -> int:
    """Return the current effective log level for the zall root logger."""
    return logging.getLogger("zall").level