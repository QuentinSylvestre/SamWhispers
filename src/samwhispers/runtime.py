"""Runtime metadata sidecar for topology-aware lifecycle control.

Writes a JSON file with PID, web topology, CSRF token, and launch args so CLI
commands (stop/restart) can discover how to reach the running instance. The
token is only persisted when owner-private file permissions can be verified.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from samwhispers.history import resolve_data_dir

_VERSION = 1


def metadata_path() -> Path:
    return resolve_data_dir() / "runtime.json"


@dataclass
class RuntimeMetadata:
    version: int = _VERSION
    pid: int = 0
    web_enabled: bool = True
    web_host: str = "127.0.0.1"
    web_port: int | None = 7891
    config_path: str | None = None
    launch_args: list[str] = field(default_factory=list)
    executable: str = ""
    cwd: str = ""
    created_at: float = 0.0
    csrf_token: str | None = None


def _permissions_private(path: Path) -> bool:
    """Check and enforce owner-private permissions. Returns True if private."""
    if sys.platform == "win32":
        return _win_check_private(path)
    else:
        return _posix_check_private(path)


def _posix_check_private(path: Path) -> bool:
    """POSIX: file must be owned by current user with mode 0o600."""
    try:
        st = path.stat()
        if st.st_uid != os.getuid():  # type: ignore[attr-defined]
            return False
        if st.st_mode & 0o077:
            os.chmod(str(path), 0o600)
            st = path.stat()
            return (st.st_mode & 0o077) == 0
        return True
    except OSError:
        return False


def _win_startupinfo() -> Any:
    """Return a STARTUPINFO that hides any console window."""
    import subprocess
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0  # SW_HIDE
    return si


def _win_check_private(path: Path) -> bool:
    """Windows: reject broad ACEs (Everyone, Users, Authenticated Users)."""
    try:
        import subprocess

        result = subprocess.run(
            ["icacls", str(path)],
            capture_output=True, text=True, timeout=5,
            creationflags=0x08000000,
            startupinfo=_win_startupinfo(),
        )
        if result.returncode != 0:
            return False
        output = result.stdout.lower()
        broad_sids = ("everyone", "users", "authenticated users", "\\users")
        for sid in broad_sids:
            if sid in output:
                return False
        return True
    except Exception:
        return False


def _set_private(path: Path) -> bool:
    """Set owner-private permissions. Returns True on success."""
    if sys.platform == "win32":
        return _win_set_private(path)
    else:
        try:
            os.chmod(str(path), 0o600)
            return True
        except OSError:
            return False


def _win_set_private(path: Path) -> bool:
    """Windows: restrict ACL to current user + SYSTEM + Administrators."""
    try:
        import subprocess

        username = os.environ.get("USERNAME", "")
        if not username:
            return False
        result = subprocess.run(
            ["icacls", str(path), "/inheritance:r",
             "/grant:r", f'"{username}":F',
             "/grant:r", "SYSTEM:F",
             "/grant:r", "Administrators:F"],
            capture_output=True, text=True, timeout=5,
            creationflags=0x08000000,
            startupinfo=_win_startupinfo(),
        )
        return result.returncode == 0
    except Exception:
        return False


def write_metadata(meta: RuntimeMetadata) -> None:
    """Atomically write metadata. Omit token if permissions cannot be secured."""
    path = metadata_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    data = asdict(meta)

    # Write to temp then rename for atomicity
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        os.write(fd, json.dumps(data, indent=2).encode("utf-8"))
        os.close(fd)
        fd = -1

        tmp_path = Path(tmp)
        if not _set_private(tmp_path):
            # Can't secure permissions — omit token
            data["csrf_token"] = None
            tmp_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

        # Atomic replace (Windows needs target removed first)
        if sys.platform == "win32" and path.exists():
            path.unlink()
        os.replace(tmp, str(path))

        # Verify final permissions if token present
        if data.get("csrf_token") and not _permissions_private(path):
            # Strip token from persisted file
            data["csrf_token"] = None
            path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except BaseException:
        if fd >= 0:
            os.close(fd)
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_metadata() -> RuntimeMetadata | None:
    """Read metadata, returning None if missing/corrupt."""
    path = metadata_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or data.get("version") != _VERSION:
        return None
    # Strip token if file permissions are not private
    if data.get("csrf_token") and not _permissions_private(path):
        data["csrf_token"] = None
    try:
        return RuntimeMetadata(
            version=data.get("version", _VERSION),
            pid=int(data.get("pid", 0)),
            web_enabled=bool(data.get("web_enabled", True)),
            web_host=str(data.get("web_host", "127.0.0.1")),
            web_port=data.get("web_port"),
            config_path=data.get("config_path"),
            launch_args=data.get("launch_args", []),
            executable=str(data.get("executable", "")),
            cwd=str(data.get("cwd", "")),
            created_at=float(data.get("created_at", 0)),
            csrf_token=data.get("csrf_token"),
        )
    except (TypeError, ValueError):
        return None


def delete_metadata() -> None:
    """Remove the metadata file if it exists."""
    try:
        metadata_path().unlink()
    except OSError:
        pass


def is_pid_alive(pid: int) -> bool:
    """Check if a PID is alive."""
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def is_samwhispers_process(pid: int) -> bool:
    """Verify a PID belongs to a samwhispers process. Returns False if unverifiable."""
    if not is_pid_alive(pid):
        return False
    try:
        if sys.platform == "win32":
            import subprocess

            result = subprocess.run(
                ["powershell", "-NoProfile", "-c",
                 f"(Get-CimInstance Win32_Process -Filter 'ProcessId={pid}').CommandLine"],
                capture_output=True, text=True, timeout=5,
                creationflags=0x08000000,
                startupinfo=_win_startupinfo(),
            )
            return "samwhispers" in result.stdout.lower()
        else:
            cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().decode("utf-8", errors="replace")
            return "samwhispers" in cmdline.lower()
    except Exception:
        # Unreadable = unverified, NOT ownership
        return False


def validate_metadata(meta: RuntimeMetadata) -> bool:
    """Validate that metadata refers to a live, verified SamWhispers instance.

    Returns True only when PID is alive AND command matches SamWhispers AND
    the lock file agrees.
    """
    if not is_pid_alive(meta.pid):
        delete_metadata()
        return False
    if not is_samwhispers_process(meta.pid):
        # PID alive but not samwhispers (reuse) — clean up
        from samwhispers.singleinstance import is_running

        if not is_running():
            delete_metadata()
        return False
    # Verify lock is held
    from samwhispers.singleinstance import is_running

    if not is_running():
        delete_metadata()
        return False
    return True
