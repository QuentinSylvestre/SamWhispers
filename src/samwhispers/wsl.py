"""WSL detection and Windows interop utilities."""

from __future__ import annotations

import functools
import logging
import os
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
    # Common fallback locations (use isfile, not which -- .exe files in WSL often lack executable bit)
    for prefix in ["/mnt/c/Windows/System32", "/mnt/c/Windows/System32/WindowsPowerShell/v1.0"]:
        candidate = f"{prefix}/{name}"
        if os.path.isfile(candidate):
            return candidate
    return None
