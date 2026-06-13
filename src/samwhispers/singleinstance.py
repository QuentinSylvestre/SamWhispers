"""Single-instance lock to prevent a second supervisor from starting.

Uses an OS advisory file lock (``fcntl`` on POSIX, ``msvcrt`` on Windows) held
for the process lifetime. The OS releases it automatically when the process
exits -- even on crash -- so there are no stale locks to clean up.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from samwhispers.history import resolve_data_dir


def lock_path() -> Path:
    return resolve_data_dir() / "supervisor.lock"


class InstanceLock:
    """An exclusive, non-blocking file lock. Hold the instance to keep the lock."""

    def __init__(self) -> None:
        self._fd: int | None = None

    def acquire(self) -> bool:
        """Try to take the lock. Returns True if acquired, False if held elsewhere."""
        path = lock_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o644)
        try:
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            return False
        self._fd = fd
        return True

    def release(self) -> None:
        if self._fd is None:
            return
        try:
            if sys.platform == "win32":
                import msvcrt

                os.lseek(self._fd, 0, os.SEEK_SET)
                msvcrt.locking(self._fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._fd, fcntl.LOCK_UN)
        except OSError:
            pass
        finally:
            os.close(self._fd)
            self._fd = None


def is_running() -> bool:
    """Whether another instance currently holds the lock."""
    probe = InstanceLock()
    if probe.acquire():
        probe.release()
        return False
    return True
