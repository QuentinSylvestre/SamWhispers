#!/usr/bin/env bash
# Launcher for SamWhispers under WSL.
# Same as the Linux launcher -- kept as a separate entry point so
# WSL-specific tweaks (e.g. DISPLAY, interop checks) can live here
# without cluttering the native Linux script.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="$SCRIPT_DIR/.venv"

if [ ! -d "$VENV" ]; then
    echo "Error: venv not found at $VENV" >&2
    echo "Run 'make setup' first." >&2
    exit 1
fi

# Verify Windows interop is available (required for hotkeys & clipboard).
if [ ! -x /mnt/c/Windows/System32/clip.exe ] 2>/dev/null; then
    echo "Warning: clip.exe not found. Windows interop may be disabled." >&2
fi

exec "$VENV/bin/python" -m samwhispers "$@"
