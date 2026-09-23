"""zall CLI entry point."""

from zall.cli.app import main, console_main

__all__ = ["main", "console_main"]

if __name__ == "__main__":
    raise SystemExit(console_main())