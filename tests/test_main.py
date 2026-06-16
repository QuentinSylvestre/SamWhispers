"""Tests for the top-level entry dispatcher."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import respx

from samwhispers.app import SamWhispers
from samwhispers.config import AppConfig
import samwhispers.__main__ as entry


def _make_app_with_real_providers(config: AppConfig) -> SamWhispers:
    """Create app with real cleanup/translation providers but mocked I/O."""
    with (
        patch("samwhispers.app.AudioRecorder") as mock_rec,
        patch("samwhispers.app.WhisperClient") as mock_wc,
        patch("samwhispers.wsl.is_wsl", return_value=False),
    ):
        app = SamWhispers(config)
        app.recorder = mock_rec.return_value
        app.whisper = mock_wc.return_value
    app.injector = MagicMock()
    app.hotkey_listener = MagicMock()
    return app


def test_bare_invocation_runs_supervisor() -> None:
    with (
        patch.object(sys, "argv", ["samwhispers"]),
        patch("samwhispers.supervisor.main") as sup_main,
    ):
        entry.main()
    sup_main.assert_called_once()


def test_supervisor_args_pass_through() -> None:
    with (
        patch.object(sys, "argv", ["samwhispers", "--no-tray"]),
        patch("samwhispers.supervisor.main") as sup_main,
    ):
        entry.main()
    sup_main.assert_called_once()  # supervisor parses its own args


def test_start_subcommand_strips_start_token() -> None:
    """'samwhispers start --foreground' passes args to supervisor without 'start'."""
    with (
        patch.object(sys, "argv", ["samwhispers", "start", "--foreground", "-v"]),
        patch("samwhispers.supervisor.main") as sup_main,
    ):
        entry.main()
    sup_main.assert_called_once()
    # sys.argv should not contain 'start'
    assert "start" not in sys.argv


def test_worker_subcommand_dispatches() -> None:
    with (
        patch.object(sys, "argv", ["samwhispers", "worker", "--unmanaged-server"]),
        patch.object(entry, "_run_worker") as run_worker,
    ):
        entry.main()
    run_worker.assert_called_once()
    args = run_worker.call_args[0][0]
    assert args.unmanaged_server is True


def test_stop_uses_metadata_http(tmp_path: Path) -> None:
    """Stop reads metadata and uses HTTP when web enabled."""
    from samwhispers.runtime import RuntimeMetadata

    meta = RuntimeMetadata(
        pid=os.getpid(),
        web_enabled=True,
        web_host="127.0.0.1",
        web_port=7891,
        csrf_token="tok",
        created_at=1.0,
    )
    with (
        patch("samwhispers.runtime.read_metadata", return_value=meta),
        patch("samwhispers.runtime.validate_metadata", return_value=True),
        patch("samwhispers.__main__._http_post", return_value=True) as hp,
        patch("samwhispers.singleinstance.is_running", return_value=False),
        patch("samwhispers.runtime.delete_metadata"),
    ):
        result = entry._do_stop()
    assert result is True
    hp.assert_called_once_with("127.0.0.1", 7891, "/api/supervisor/shutdown", "tok")


def test_stop_falls_back_to_pid_kill(tmp_path: Path) -> None:
    """Stop falls back to PID kill when HTTP fails."""
    from samwhispers.runtime import RuntimeMetadata

    meta = RuntimeMetadata(
        pid=12345,
        web_enabled=True,
        web_host="127.0.0.1",
        web_port=7891,
        csrf_token="tok",
        created_at=1.0,
    )
    with (
        patch("samwhispers.runtime.read_metadata", return_value=meta),
        patch("samwhispers.runtime.validate_metadata", return_value=True),
        patch("samwhispers.__main__._http_post", return_value=False),
        patch("samwhispers.runtime.is_pid_alive", return_value=True),
        patch("samwhispers.runtime.is_samwhispers_process", return_value=True),
        patch("samwhispers.__main__._force_kill") as fk,
        patch("samwhispers.runtime.delete_metadata"),
    ):
        result = entry._do_stop()
    assert result is True
    fk.assert_called_once_with(12345)


def test_stop_no_web_uses_pid(tmp_path: Path) -> None:
    """Stop on --no-web instance goes straight to PID kill."""
    from samwhispers.runtime import RuntimeMetadata

    meta = RuntimeMetadata(
        pid=12345,
        web_enabled=False,
        web_port=None,
        created_at=1.0,
    )
    with (
        patch("samwhispers.runtime.read_metadata", return_value=meta),
        patch("samwhispers.runtime.validate_metadata", return_value=True),
        patch("samwhispers.runtime.is_pid_alive", return_value=True),
        patch("samwhispers.runtime.is_samwhispers_process", return_value=True),
        patch("samwhispers.__main__._force_kill") as fk,
        patch("samwhispers.runtime.delete_metadata"),
    ):
        result = entry._do_stop()
    assert result is True
    fk.assert_called_once_with(12345)


def test_restart_uses_metadata_http() -> None:
    """Restart uses HTTP endpoint when web enabled."""
    from samwhispers.runtime import RuntimeMetadata

    meta = RuntimeMetadata(
        pid=os.getpid(),
        web_enabled=True,
        web_host="127.0.0.1",
        web_port=9000,
        csrf_token="tok",
        created_at=1.0,
    )
    with (
        patch("samwhispers.runtime.read_metadata", return_value=meta),
        patch("samwhispers.runtime.validate_metadata", return_value=True),
        patch("samwhispers.__main__._http_post", return_value=True) as hp,
    ):
        entry._do_restart()
    hp.assert_called_once_with("127.0.0.1", 9000, "/api/supervisor/restart", "tok")


@respx.mock
def test_cleanup_provider_failure_returns_original_text() -> None:
    """Cleanup API failures keep the original transcription in the output path."""
    config = AppConfig()
    config.whisper.managed = False
    config.cleanup.enabled = True
    config.cleanup.provider = "openai"
    config.cleanup.openai.api_key = "sk-test"
    respx.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=httpx.Response(500, text="provider down")
    )
    app = _make_app_with_real_providers(config)
    app.whisper.transcribe.return_value = "hello world"

    app._process_recording(b"\x00" * 20000)

    app.injector.inject.assert_called_once_with("hello world\n")
    app.cleanup.close()
    app.translator.close()


@respx.mock
def test_translation_provider_failure_returns_original_text() -> None:
    """Translation API failures keep the original transcription in the output path."""
    config = AppConfig()
    config.whisper.managed = False
    config.cleanup.provider = "openai"
    config.cleanup.openai.api_key = "sk-test"
    config.translation.enabled = True
    config.translation.target_language = "fr"
    respx.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=httpx.Response(500, text="provider down")
    )
    app = _make_app_with_real_providers(config)
    app.whisper.transcribe.return_value = "hello world"

    app._process_recording(b"\x00" * 20000)

    app.injector.inject.assert_called_once_with("hello world\n")
    app.cleanup.close()
    app.translator.close()
