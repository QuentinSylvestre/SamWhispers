"""Install/remove a login-autostart entry for the supervisor.

Cross-platform: a systemd *user* service on Linux, a Task Scheduler "at logon"
task on Windows. Replaces the manual unit-editing in docs/STARTUP.md with
``samwhispers-autostart enable``.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

SERVICE_NAME = "samwhispers"


def supervisor_command() -> str:
    """Command that launches the supervisor, preferring the installed script.

    On Windows, prefer ``pythonw`` so no console window is shown at logon.
    """
    exe = shutil.which("samwhispers-supervisor")
    if exe:
        return f'"{exe}"' if " " in exe else exe
    python = sys.executable
    if sys.platform == "win32":
        pythonw = Path(python).with_name("pythonw.exe")
        if pythonw.exists():
            python = str(pythonw)
    return f'"{python}" -m samwhispers.supervisor'


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
    subprocess.run(
        ["systemctl", "--user", "enable", "--now", f"{SERVICE_NAME}.service"], check=True
    )
    print(f"Autostart enabled. Status: systemctl --user status {SERVICE_NAME}")


def _disable_linux() -> None:
    subprocess.run(
        ["systemctl", "--user", "disable", "--now", f"{SERVICE_NAME}.service"], check=False
    )
    systemd_unit_path().unlink(missing_ok=True)
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
    print("Autostart disabled.")


def _status_linux() -> None:
    subprocess.run(["systemctl", "--user", "status", f"{SERVICE_NAME}.service"], check=False)


# --- Windows (Task Scheduler) ---------------------------------------------


def _enable_windows() -> None:
    subprocess.run(
        [
            "schtasks",
            "/Create",
            "/SC",
            "ONLOGON",
            "/TN",
            SERVICE_NAME,
            "/TR",
            supervisor_command(),
            "/F",
        ],
        check=True,
    )
    print(f"Autostart enabled (Task Scheduler task '{SERVICE_NAME}').")


def _disable_windows() -> None:
    subprocess.run(["schtasks", "/Delete", "/TN", SERVICE_NAME, "/F"], check=False)
    print("Autostart disabled.")


def _status_windows() -> None:
    subprocess.run(["schtasks", "/Query", "/TN", SERVICE_NAME], check=False)


def _dispatch(action: str) -> None:
    linux = {"enable": _enable_linux, "disable": _disable_linux, "status": _status_linux}
    windows = {"enable": _enable_windows, "disable": _disable_windows, "status": _status_windows}
    table = windows if sys.platform == "win32" else linux
    table[action]()


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="samwhispers-autostart",
        description="Enable/disable launching SamWhispers at login.",
    )
    parser.add_argument("action", choices=["enable", "disable", "status"])
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
