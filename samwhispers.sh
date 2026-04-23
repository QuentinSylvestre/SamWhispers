#!/usr/bin/env bash
# Launcher for SamWhispers (Linux / macOS).
# Activates the project venv and forwards all arguments.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="$SCRIPT_DIR/.venv"

if [ ! -d "$VENV" ]; then
    echo "Error: venv not found at $VENV" >&2
    echo "Run 'make setup' first." >&2
    exit 1
fi

exec "$VENV/bin/python" -m samwhispers "$@"
