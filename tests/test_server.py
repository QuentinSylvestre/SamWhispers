"""Tests for whisper-server subprocess manager."""

from __future__ import annotations

import sys
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from samwhispers.config import WhisperConfig
from samwhispers.server import WhisperServerManager, _resolve_server_bin


def _make_config(**overrides: object) -> WhisperConfig:
    defaults = {
        "server_url": "http://127.0.0.1:9999",
        "languages": ["auto"],
        "managed": True,
        "server_bin": "/fake/whisper-server",
        "model_path": "/fake/model.bin",
    }
    defaults.update(overrides)
    return WhisperConfig(**defaults)  # type: ignore[arg-type]


def test_resolve_server_bin_existing(tmp_path: Path) -> None:
    """Existing path is resolved to absolute."""
    bin_file = tmp_path / "whisper-server"
    bin_file.write_bytes(b"fake")
    result = _resolve_server_bin(str(bin_file))
    assert result == str(bin_file.resolve())


def test_resolve_server_bin_windows_variant(tmp_path: Path) -> None:
    """On Windows, finds Release/*.exe variant when plain path missing."""
    if sys.platform != "win32":
        pytest.skip("Windows-only test")
    release_dir = tmp_path / "bin" / "Release"
    release_dir.mkdir(parents=True)
    exe = release_dir / "whisper-server.exe"
    exe.write_bytes(b"fake")
    result = _resolve_server_bin(str(tmp_path / "bin" / "whisper-server"))
    assert result == str(exe.resolve())


def test_stop_idempotent() -> None:
    """Calling stop() twice does not raise."""
    config = _make_config()
    with patch("samwhispers.server.atexit"):
        mgr = WhisperServerManager(config)
    mgr.stop()
    mgr.stop()  # should not raise


def test_stop_concurrent_safety() -> None:
    """Two threads calling stop() simultaneously does not raise."""
    config = _make_config()
    with patch("samwhispers.server.atexit"):
        mgr = WhisperServerManager(config)
    mock_proc = MagicMock()
    mock_proc.poll.return_value = None
    mock_proc.wait.return_value = 0
    mgr._proc = mock_proc

    errors: list[Exception] = []

    def call_stop() -> None:
        try:
            mgr.stop()
        except Exception as e:
            errors.append(e)

    t1 = threading.Thread(target=call_stop)
    t2 = threading.Thread(target=call_stop)
    t1.start()
    t2.start()
    t1.join(timeout=5)
    t2.join(timeout=5)
    assert not errors


def test_monitor_loop_exits_after_max_restarts() -> None:
    """Monitor loop gives up after _MAX_RESTARTS failures."""
    config = _make_config()
    with patch("samwhispers.server.atexit"):
        mgr = WhisperServerManager(config)

    # Create a mock process that always appears crashed
    mock_proc = MagicMock()
    mock_proc.poll.return_value = 1
    mock_proc.returncode = 1
    mock_proc.wait.return_value = None
    mgr._proc = mock_proc

    # Make _spawn and _wait_ready always fail so restarts exhaust
    with (
        patch.object(mgr, "_spawn") as mock_spawn,
        patch.object(mgr, "_wait_ready", side_effect=RuntimeError("fail")),
    ):
        mgr._monitor_loop()

    # Loop exited naturally (not via _stop_event) after first failed restart
    assert not mgr._stop_event.is_set()
    mock_spawn.assert_called_once()
