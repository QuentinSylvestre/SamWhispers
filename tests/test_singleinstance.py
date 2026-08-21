"""Tests for the single-instance file lock."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from samwhispers import singleinstance as si


def _with_lock_path(tmp_path: Path):  # type: ignore[no-untyped-def]
    return patch.object(si, "lock_path", return_value=tmp_path / "supervisor.lock")


def _with_pid_path(tmp_path: Path):  # type: ignore[no-untyped-def]
    return patch.object(si, "pid_path", return_value=tmp_path / "supervisor.pid")


def test_acquire_then_second_fails(tmp_path: Path) -> None:
    with _with_lock_path(tmp_path):
        first = si.InstanceLock()
        assert first.acquire() is True
        second = si.InstanceLock()
        assert second.acquire() is False  # held by `first`
        first.release()
        # now it's free again
        assert second.acquire() is True
        second.release()


def test_is_running_reflects_lock(tmp_path: Path) -> None:
    with _with_lock_path(tmp_path):
        assert si.is_running() is False  # nothing holds it
        holder = si.InstanceLock()
        assert holder.acquire() is True
        assert si.is_running() is True  # holder has it
        holder.release()
        assert si.is_running() is False


def test_release_is_safe_without_acquire(tmp_path: Path) -> None:
    with _with_lock_path(tmp_path):
        si.InstanceLock().release()  # must not raise


# ---------------------------------------------------------------------------
# 4a: Concurrent lock race
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.skipif(sys.platform != "win32", reason="msvcrt lock test (Windows only)")
def test_concurrent_foreground_only_one_wins(tmp_path: Path) -> None:
    """Exactly one of N concurrent processes must acquire the InstanceLock."""
    lock_file = tmp_path / "supervisor.lock"
    go_time = time.time() + 3.0  # allow for cold-import on slow CI
    N = 4

    # Each child writes its result to its own file to avoid shared-file write races.
    script = textwrap.dedent(
        f"""\
        import os, sys, time, json, pathlib
        # Add src to path so InstanceLock is importable from the source tree
        sys.path.insert(0, r"{os.path.join(os.path.dirname(__file__), '..', 'src')}")
        from samwhispers import singleinstance as si
        import unittest.mock as mock

        child_id = int(sys.argv[1])
        go_time = {go_time}
        if time.time() < go_time:
            time.sleep(go_time - time.time())

        with mock.patch.object(si, "lock_path", return_value=pathlib.Path(r"{lock_file}")):
            lock = si.InstanceLock()
            result = lock.acquire()
            if result:
                time.sleep(0.3)
                lock.release()

        out = pathlib.Path(r"{tmp_path}") / f"result_{{child_id}}.json"
        out.write_text(json.dumps(result))
        """
    )
    script_path = tmp_path / "child.py"
    script_path.write_text(script)

    procs = [
        subprocess.Popen([sys.executable, str(script_path), str(i)])
        for i in range(N)
    ]
    for p in procs:
        try:
            p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait()

    results = [
        json.loads((tmp_path / f"result_{i}.json").read_text())
        for i in range(N)
    ]
    assert len(results) == N, f"Expected {N} results, got {len(results)}"
    assert results.count(True) == 1, f"Expected exactly 1 lock winner, got {results}"


# ---------------------------------------------------------------------------
# 4b: write_pid / read_pid coverage
# ---------------------------------------------------------------------------


def test_write_and_read_pid(tmp_path: Path) -> None:
    with _with_pid_path(tmp_path):
        si.write_pid()
        assert si.read_pid() == os.getpid()


def test_read_pid_missing(tmp_path: Path) -> None:
    with _with_pid_path(tmp_path):
        assert si.read_pid() is None


def test_pid_cleanup_via_unlink(tmp_path: Path) -> None:
    """pid_path().unlink(missing_ok=True) removes the file; missing_ok tolerates absence."""
    with _with_pid_path(tmp_path):
        si.write_pid()
        assert si.pid_path().exists()
        si.pid_path().unlink(missing_ok=True)
        assert not si.pid_path().exists()
        # Second call must not raise (missing_ok=True)
        si.pid_path().unlink(missing_ok=True)
