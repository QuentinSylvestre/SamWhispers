"""Tests for runtime metadata sidecar."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from samwhispers.runtime import (
    RuntimeMetadata,
    delete_metadata,
    is_pid_alive,
    is_samwhispers_process,
    read_metadata,
    validate_metadata,
    write_metadata,
)


@pytest.fixture(autouse=True)
def _isolated_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirect runtime metadata to temp dir."""
    monkeypatch.setattr("samwhispers.runtime.metadata_path", lambda: tmp_path / "runtime.json")
    # On Windows, avoid spawning icacls subprocesses that can leave zombie handles
    if sys.platform == "win32":
        monkeypatch.setattr("samwhispers.runtime._set_private", lambda p: True)
        monkeypatch.setattr("samwhispers.runtime._permissions_private", lambda p: True)


def test_write_and_read_roundtrip(tmp_path: Path) -> None:
    meta = RuntimeMetadata(
        pid=12345,
        web_enabled=True,
        web_host="127.0.0.1",
        web_port=7891,
        config_path="/tmp/config.toml",
        launch_args=["samwhispers-supervisor", "--foreground"],
        executable=sys.executable,
        cwd="/tmp",
        created_at=1000.0,
        csrf_token="test-token-123",
    )
    write_metadata(meta)
    loaded = read_metadata()
    assert loaded is not None
    assert loaded.pid == 12345
    assert loaded.web_enabled is True
    assert loaded.web_port == 7891
    assert loaded.config_path == "/tmp/config.toml"
    assert loaded.launch_args == ["samwhispers-supervisor", "--foreground"]
    assert loaded.executable == sys.executable
    assert loaded.cwd == "/tmp"
    assert loaded.created_at == 1000.0


def test_read_returns_none_when_missing() -> None:
    assert read_metadata() is None


def test_read_returns_none_on_corrupt_json(tmp_path: Path) -> None:
    path = tmp_path / "runtime.json"
    path.write_text("not json", encoding="utf-8")
    with patch("samwhispers.runtime.metadata_path", return_value=path):
        assert read_metadata() is None


def test_read_returns_none_on_wrong_version(tmp_path: Path) -> None:
    path = tmp_path / "runtime.json"
    path.write_text(json.dumps({"version": 999}), encoding="utf-8")
    with patch("samwhispers.runtime.metadata_path", return_value=path):
        assert read_metadata() is None


def test_delete_metadata_removes_file(tmp_path: Path) -> None:
    meta = RuntimeMetadata(pid=1, created_at=1.0)
    write_metadata(meta)
    path = tmp_path / "runtime.json"
    assert path.exists()
    delete_metadata()
    assert not path.exists()


def test_delete_metadata_noop_when_missing() -> None:
    delete_metadata()  # should not raise


def test_write_no_web(tmp_path: Path) -> None:
    meta = RuntimeMetadata(pid=99, web_enabled=False, web_port=None, created_at=2.0)
    write_metadata(meta)
    loaded = read_metadata()
    assert loaded is not None
    assert loaded.web_enabled is False
    assert loaded.web_port is None


def test_write_custom_port(tmp_path: Path) -> None:
    meta = RuntimeMetadata(pid=99, web_enabled=True, web_port=9000, created_at=3.0)
    write_metadata(meta)
    loaded = read_metadata()
    assert loaded is not None
    assert loaded.web_port == 9000


def test_token_omitted_when_permissions_fail(tmp_path: Path) -> None:
    meta = RuntimeMetadata(pid=99, csrf_token="secret", created_at=4.0)
    with patch("samwhispers.runtime._set_private", return_value=False):
        write_metadata(meta)
    loaded = read_metadata()
    assert loaded is not None
    assert loaded.csrf_token is None


def test_token_stripped_on_read_when_permissions_lost(tmp_path: Path) -> None:
    meta = RuntimeMetadata(pid=99, csrf_token="secret", created_at=5.0)
    write_metadata(meta)
    with patch("samwhispers.runtime._permissions_private", return_value=False):
        loaded = read_metadata()
    assert loaded is not None
    assert loaded.csrf_token is None


def test_is_pid_alive_current_process() -> None:
    assert is_pid_alive(os.getpid()) is True


def test_is_pid_alive_invalid() -> None:
    assert is_pid_alive(0) is False
    assert is_pid_alive(-1) is False


def test_is_samwhispers_process_returns_false_for_dead_pid() -> None:
    assert is_samwhispers_process(99999) is False


def test_is_samwhispers_process_returns_false_on_inspection_failure() -> None:
    with patch("samwhispers.runtime.is_pid_alive", return_value=True):
        if sys.platform == "win32":
            with patch("subprocess.run", side_effect=Exception("fail")):
                assert is_samwhispers_process(1234) is False
        else:
            with patch("pathlib.Path.read_bytes", side_effect=OSError("fail")):
                assert is_samwhispers_process(1234) is False


def test_validate_metadata_dead_pid(tmp_path: Path) -> None:
    meta = RuntimeMetadata(pid=99999, created_at=6.0)
    write_metadata(meta)
    with patch("samwhispers.singleinstance.is_running", return_value=False):
        assert validate_metadata(meta) is False
    # Should have cleaned up metadata
    assert read_metadata() is None


def test_validate_metadata_pid_reuse(tmp_path: Path) -> None:
    """PID alive but not samwhispers — metadata cleaned if lock not held."""
    meta = RuntimeMetadata(pid=os.getpid(), created_at=7.0)
    write_metadata(meta)
    with (
        patch("samwhispers.runtime.is_pid_alive", return_value=True),
        patch("samwhispers.runtime.is_samwhispers_process", return_value=False),
        patch("samwhispers.singleinstance.is_running", return_value=False),
    ):
        assert validate_metadata(meta) is False
    assert read_metadata() is None


def test_validate_metadata_lock_not_held(tmp_path: Path) -> None:
    """PID alive, is samwhispers, but lock not held — invalid."""
    meta = RuntimeMetadata(pid=os.getpid(), created_at=8.0)
    write_metadata(meta)
    with (
        patch("samwhispers.runtime.is_pid_alive", return_value=True),
        patch("samwhispers.runtime.is_samwhispers_process", return_value=True),
        patch("samwhispers.singleinstance.is_running", return_value=False),
    ):
        assert validate_metadata(meta) is False


def test_validate_metadata_all_checks_pass(tmp_path: Path) -> None:
    """Valid metadata when PID alive, is samwhispers, and lock held."""
    meta = RuntimeMetadata(pid=os.getpid(), created_at=9.0)
    write_metadata(meta)
    with (
        patch("samwhispers.runtime.is_pid_alive", return_value=True),
        patch("samwhispers.runtime.is_samwhispers_process", return_value=True),
        patch("samwhispers.singleinstance.is_running", return_value=True),
    ):
        assert validate_metadata(meta) is True
