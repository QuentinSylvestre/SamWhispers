"""Cross-platform desktop notifications."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys

log = logging.getLogger("samwhispers")


def notify(title: str, message: str) -> None:
    """Show a desktop notification. Logs warning on failure, never raises."""
    try:
        from samwhispers.wsl import is_wsl

        if is_wsl() or sys.platform == "win32":
            _notify_windows(title, message)
        else:
            _notify_linux(title, message)
    except Exception:
        log.warning("Desktop notification failed (title=%r)", title)


def check_notify_available() -> bool:
    """Check if the notification backend is available."""
    from samwhispers.wsl import is_wsl

    if is_wsl() or sys.platform == "win32":
        return True  # PowerShell is always available on Windows/WSL
    return shutil.which("notify-send") is not None


def _notify_linux(title: str, message: str) -> None:
    subprocess.run(
        ["notify-send", "--app-name=SamWhispers", title, message],
        check=True,
        timeout=5,
        capture_output=True,
    )


def _notify_windows(title: str, message: str) -> None:
    from samwhispers.wsl import is_wsl

    if is_wsl():
        from samwhispers.wsl import find_windows_exe

        ps = find_windows_exe("powershell.exe")
    else:
        ps = "powershell.exe"
    if not ps:
        log.warning("powershell.exe not found, cannot show notification")
        return
    # Pass data via environment variables to avoid PowerShell injection
    script = (
        "Add-Type -AssemblyName System.Windows.Forms;"
        "$n = New-Object System.Windows.Forms.NotifyIcon;"
        "$n.Icon = [System.Drawing.SystemIcons]::Information;"
        "$n.Visible = $true;"
        "$n.ShowBalloonTip(3000, $env:SW_TITLE, $env:SW_MSG, 'Info');"
        "Start-Sleep -Milliseconds 3100;"
        "$n.Dispose()"
    )
    subprocess.Popen(
        [ps, "-NoProfile", "-WindowStyle", "Hidden", "-c", script],
        env={**os.environ, "SW_TITLE": title, "SW_MSG": message},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
