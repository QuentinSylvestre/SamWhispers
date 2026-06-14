"""Cross-platform desktop notifications."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys

log = logging.getLogger("samwhispers")


def notify(title: str, message: str, on_click_url: str | None = None) -> None:
    """Show a desktop notification. Logs warning on failure, never raises."""
    try:
        from samwhispers.wsl import is_wsl

        if is_wsl() or sys.platform == "win32":
            _notify_windows(title, message, on_click_url)
        else:
            _notify_linux(title, message, on_click_url)
    except Exception:
        log.warning("Desktop notification failed (title=%r)", title)


def check_notify_available() -> bool:
    """Check if the notification backend is available."""
    from samwhispers.wsl import is_wsl

    if is_wsl() or sys.platform == "win32":
        return True  # PowerShell is always available on Windows/WSL
    return shutil.which("notify-send") is not None


def _notify_linux(title: str, message: str, on_click_url: str | None = None) -> None:
    cmd = ["notify-send", "--app-name=SamWhispers", title, message]
    if on_click_url:
        cmd += ["--action=default=Open"]
    result = subprocess.run(cmd, check=True, timeout=5, capture_output=True, text=True)
    if on_click_url and "default" in (result.stdout or ""):
        subprocess.Popen(["xdg-open", on_click_url])


def _notify_windows(title: str, message: str, on_click_url: str | None = None) -> None:
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
    if on_click_url:
        script = (
            "Add-Type -AssemblyName System.Windows.Forms;"
            "$n = New-Object System.Windows.Forms.NotifyIcon;"
            "$n.Icon = [System.Drawing.SystemIcons]::Information;"
            "$n.Visible = $true;"
            "$n.Add_BalloonTipClicked({Start-Process $env:SW_URL});"
            "$n.ShowBalloonTip(5000, $env:SW_TITLE, $env:SW_MSG, 'Info');"
            "Start-Sleep -Milliseconds 5100;"
            "$n.Dispose()"
        )
        env = {**os.environ, "SW_TITLE": title, "SW_MSG": message, "SW_URL": on_click_url}
    else:
        script = (
            "Add-Type -AssemblyName System.Windows.Forms;"
            "$n = New-Object System.Windows.Forms.NotifyIcon;"
            "$n.Icon = [System.Drawing.SystemIcons]::Information;"
            "$n.Visible = $true;"
            "$n.ShowBalloonTip(3000, $env:SW_TITLE, $env:SW_MSG, 'Info');"
            "Start-Sleep -Milliseconds 3100;"
            "$n.Dispose()"
        )
        env = {**os.environ, "SW_TITLE": title, "SW_MSG": message}
    subprocess.Popen(
        [ps, "-NoProfile", "-WindowStyle", "Hidden", "-c", script],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
