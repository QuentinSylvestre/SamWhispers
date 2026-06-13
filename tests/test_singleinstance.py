"""Tests for the single-instance file lock."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from samwhispers import singleinstance as si


def _with_lock_path(tmp_path: Path):  # type: ignore[no-untyped-def]
    return patch.object(si, "lock_path", return_value=tmp_path / "supervisor.lock")


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
