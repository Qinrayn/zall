"""zall CLI entry point."""

from zall.cli.app import main

__all__ = ["main"]

if __name__ == "__main__":
    raise SystemExit(main())