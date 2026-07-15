#!/usr/bin/env bash
# zall install.sh — One-command install for Linux/macOS
# Usage: curl -fsSL https://raw.githubusercontent.com/.../install.sh | bash
# Or:    ./scripts/install.sh

set -euo pipefail

ZALL_HOME="${ZALL_HOME:-$HOME/.zall}"
ZALL_VERSION="${ZALL_VERSION:-latest}"

echo "  ╔══════════════════════════════════════╗"
echo "  ║   zall — Falsifiable Coding Agent    ║"
echo "  ╚══════════════════════════════════════╝"
echo ""

# ── 1. Check Python ──
command -v python3 >/dev/null 2>&1 || { echo "  ✗ python3 not found. Install Python >= 3.10 first."; exit 1; }
PYVER=$(python3 --version 2>&1 | awk '{print $2}')
PYMAJ=$(echo "$PYVER" | cut -d. -f1)
PYMIN=$(echo "$PYVER" | cut -d. -f2)
if [ "$PYMAJ" -lt 3 ] || { [ "$PYMAJ" -eq 3 ] && [ "$PYMIN" -lt 10 ]; }; then
    echo "  ✗ Python >= 3.10 required, found $PYVER"
    exit 1
fi
echo "  ✓ Python $PYVER"

# ── 2. Create virtual environment ──
if [ -d "$ZALL_HOME/venv" ]; then
    echo "  · zall venv already exists at $ZALL_HOME/venv"
else
    echo "  · Creating virtual environment at $ZALL_HOME/venv ..."
    python3 -m venv "$ZALL_HOME/venv"
fi

# ── 3. Install zall ──
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "  · Installing zall from $PROJECT_DIR ..."
"$ZALL_HOME/venv/bin/pip" install --quiet --upgrade pip
"$ZALL_HOME/venv/bin/pip" install --quiet -e "$PROJECT_DIR"

echo "  ✓ zall installed"

# ── 4. Create symlink ──
SYMLINK_DIR="${HOME}/.local/bin"
mkdir -p "$SYMLINK_DIR"
SYMLINK_PATH="$SYMLINK_DIR/zall"
if [ ! -L "$SYMLINK_PATH" ] || [ ! -e "$SYMLINK_PATH" ]; then
    ln -sf "$ZALL_HOME/venv/bin/zall" "$SYMLINK_PATH"
    echo "  ✓ Created symlink: $SYMLINK_PATH"
fi

# ── 5. Add to PATH if needed ──
case ":$PATH:" in
    *":$SYMLINK_DIR:"*) ;;
    *)
        echo ""
        echo "  ⚠  $SYMLINK_DIR is not in your PATH."
        echo "     Add this to your ~/.bashrc or ~/.zshrc:"
        echo "       export PATH=\"\$PATH:$SYMLINK_DIR\""
        ;;
esac

# ── 6. Run onboarding ──
echo ""
echo "  Running first-time setup..."
"$ZALL_HOME/venv/bin/zall" init 2>/dev/null || true

echo ""
echo "  ╔══════════════════════════════════════╗"
echo "  ║   zall installed successfully!        ║"
echo "  ║                                      ║"
echo "  ║   Run 'zall' to start the REPL       ║"
echo "  ║   Run 'zall \"task\"' for one-shot     ║"
echo "  ║   Run 'zall --help' for options       ║"
echo "  ╚══════════════════════════════════════╝"