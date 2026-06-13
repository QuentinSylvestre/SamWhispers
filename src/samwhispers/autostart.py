"""Install/manage a login-autostart entry for the supervisor.

Cross-platform: a systemd *user* service on Linux, a per-user Startup-folder
shortcut on Windows (no admin / Task Scheduler needed -- the latter is blocked
on many managed machines). ``enable``/``disable`` only manage the autostart
entry (they don't launch or kill the app); ``start``/``stop`` control a running
background instance; run it in the foreground with ``samwhispers-supervisor``.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

SERVICE_NAME = "samwhispers"


def supervisor_command() -> str:
    """Command that launches the supervisor.

    On Windows, use ``pythonw`` so no console window appears (at logon or when
    launched detached). On Linux, prefer the installed console script.
    """
    if sys.platform == "win32":
        python = sys.executable
        pythonw = Path(python).with_name("pythonw.exe")
        exe = str(pythonw) if pythonw.exists() else python
        return f'"{exe}" -m samwhispers.supervisor'
    exe = shutil.which("samwhispers-supervisor")
    if exe:
        return f'"{exe}"' if " " in exe else exe
    return f'"{sys.executable}" -m samwhispers.supervisor'


# --- Linux (systemd user service) -----------------------------------------


def systemd_unit_path() -> Path:
    return Path.home() / ".config" / "systemd" / "user" / f"{SERVICE_NAME}.service"


def systemd_unit_text(exec_start: str) -> str:
    return (
        "[Unit]\n"
        "Description=SamWhispers voice-to-text (tray + worker)\n"
        "After=graphical-session.target\n"
        "PartOf=graphical-session.target\n\n"
        "[Service]\n"
        "Type=simple\n"
        f"ExecStart={exec_start}\n"
        "Restart=on-failure\n"
        "RestartSec=5\n\n"
        "[Install]\n"
        "WantedBy=graphical-session.target\n"
    )


def _enable_linux() -> None:
    path = systemd_unit_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(systemd_unit_text(supervisor_command()), encoding="utf-8")
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    # Best-effort: make the session display available to the user service.
    subprocess.run(
        ["systemctl", "--user", "import-environment", "DISPLAY", "XAUTHORITY"], check=False
    )
    subprocess.run(["systemctl", "--user", "enable", f"{SERVICE_NAME}.service"], check=True)
    print("Autostart configured (starts at next login). Run now with: samwhispers-supervisor")


def _disable_linux() -> None:
    subprocess.run(["systemctl", "--user", "disable", f"{SERVICE_NAME}.service"], check=False)
    systemd_unit_path().unlink(missing_ok=True)
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
    print("Autostart removed (a running instance keeps running; quit it from the tray).")


def _start_linux() -> None:
    subprocess.run(["systemctl", "--user", "start", f"{SERVICE_NAME}.service"], check=True)
    print("Started.")


def _stop_linux() -> None:
    subprocess.run(["systemctl", "--user", "stop", f"{SERVICE_NAME}.service"], check=False)
    print("Stopped.")


def _status_linux() -> None:
    subprocess.run(["systemctl", "--user", "status", f"{SERVICE_NAME}.service"], check=False)


# --- Windows (Startup folder shortcut) ------------------------------------
#
# A Task Scheduler task needs permissions that managed/corporate machines often
# deny ("Access is denied"). A shortcut in the per-user Startup folder runs at
# logon with no admin rights and no policy exception.

_DETACHED_PROCESS = 0x00000008
_CREATE_NO_WINDOW = 0x08000000

# Find our supervisor process precisely (don't touch other pythonw apps).
_FIND_PROC = (
    "Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe'\" | "
    "Where-Object { $_.CommandLine -like '*samwhispers.supervisor*' }"
)


def _windows_target_and_args() -> tuple[str, str]:
    """Return (target, args) for launching the supervisor with no console window.

    Anchored on the installed ``samwhispers-supervisor`` script so we use the
    venv's ``pythonw`` (``sys.executable`` can report the base interpreter in
    some venv setups, which wouldn't have the package importable).
    """
    candidates: list[Path] = []
    script = shutil.which("samwhispers-supervisor")
    if script:
        candidates.append(Path(script).with_name("pythonw.exe"))  # <venv>/Scripts/pythonw.exe
    candidates.append(Path(sys.executable).with_name("pythonw.exe"))
    for pythonw in candidates:
        if pythonw.exists():
            return str(pythonw), "-m samwhispers.supervisor"
    # No pythonw found -> use the console script directly (shows a brief window).
    if script:
        return script, ""
    return sys.executable, "-m samwhispers.supervisor"


def _startup_shortcut() -> Path:
    appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return (
        Path(appdata)
        / "Microsoft"
        / "Windows"
        / "Start Menu"
        / "Programs"
        / "Startup"
        / f"{SERVICE_NAME}.lnk"
    )


def _ps_quote(value: str) -> str:
    """Quote a string for a PowerShell single-quoted literal."""
    return "'" + value.replace("'", "''") + "'"


def _run_powershell(script: str, check: bool) -> None:
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], check=check)


def _create_startup_shortcut() -> None:
    lnk = _startup_shortcut()
    lnk.parent.mkdir(parents=True, exist_ok=True)
    target, args = _windows_target_and_args()
    script = (
        "$s=(New-Object -ComObject WScript.Shell).CreateShortcut(" + _ps_quote(str(lnk)) + ");"
        "$s.TargetPath=" + _ps_quote(target) + ";"
        "$s.Arguments=" + _ps_quote(args) + ";"
        "$s.Save()"
    )
    _run_powershell(script, check=True)


def _enable_windows() -> None:
    _create_startup_shortcut()
    print(f"Autostart configured (starts at next login): {_startup_shortcut()}")
    print("Run now with: samwhispers-supervisor")


def _disable_windows() -> None:
    _startup_shortcut().unlink(missing_ok=True)
    print("Autostart removed (a running instance keeps running; quit it from the tray).")


def _start_windows() -> None:
    target, args = _windows_target_and_args()
    argv = [target, *args.split()]
    subprocess.Popen(argv, creationflags=_DETACHED_PROCESS | _CREATE_NO_WINDOW, close_fds=True)
    print("Started.")


def _stop_windows() -> None:
    _run_powershell(
        _FIND_PROC + " | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }", check=False
    )
    print("Stopped.")


def _status_windows() -> None:
    lnk = _startup_shortcut()
    print(f"Startup shortcut: {'present' if lnk.exists() else 'absent'} ({lnk})")
    _run_powershell(
        _FIND_PROC + " | Select-Object ProcessId,CommandLine | Format-List", check=False
    )


def _dispatch(action: str) -> None:
    linux = {
        "enable": _enable_linux,
        "disable": _disable_linux,
        "start": _start_linux,
        "stop": _stop_linux,
        "status": _status_linux,
    }
    windows = {
        "enable": _enable_windows,
        "disable": _disable_windows,
        "start": _start_windows,
        "stop": _stop_windows,
        "status": _status_windows,
    }
    table = windows if sys.platform == "win32" else linux
    table[action]()


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="samwhispers-autostart",
        description="Install/manage launching SamWhispers at login.",
    )
    parser.add_argument("action", choices=["enable", "disable", "start", "stop", "status"])
    args = parser.parse_args()
    if sys.platform not in ("win32", "linux"):
        print(
            f"Autostart is not automated for {sys.platform}; see docs/STARTUP.md "
            "(launchd) for macOS."
        )
        raise SystemExit(1)
    try:
        _dispatch(args.action)
    except FileNotFoundError as exc:
        raise SystemExit(f"Required tool not found: {exc}")
    except subprocess.CalledProcessError as exc:
        raise SystemExit(f"Command failed: {exc}")


if __name__ == "__main__":
    main()
