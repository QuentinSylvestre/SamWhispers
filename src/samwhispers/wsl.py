"""WSL detection and Windows interop utilities."""

from __future__ import annotations

import functools
import logging
import shutil

log = logging.getLogger("samwhispers")


@functools.cache
def is_wsl() -> bool:
    """Detect if running inside Windows Subsystem for Linux."""
    try:
        with open("/proc/version") as f:
            return "microsoft" in f.read().lower()
    except OSError:
        return False


def find_windows_exe(name: str) -> str | None:
    """Find a Windows executable accessible from WSL."""
    # Try PATH first (works if Windows PATH is appended)
    path = shutil.which(name)
    if path:
        return path
    # Common fallback locations
    for prefix in ["/mnt/c/Windows/System32", "/mnt/c/Windows/System32/WindowsPowerShell/v1.0"]:
        candidate = f"{prefix}/{name}"
        if shutil.which(candidate):
            return candidate
    return None
