"""Tests for main app orchestration."""

from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

from samwhispers.app import SamWhispers, State
from samwhispers.config import AppConfig


def _make_app() -> SamWhispers:
    """Create app with all components mocked, bypassing WSL detection."""
    config = AppConfig()
    with (
        patch("samwhispers.app.AudioRecorder") as mock_rec,
        patch("samwhispers.app.WhisperClient") as mock_wc,
        patch("samwhispers.app.CleanupProvider") as mock_cp,
        patch("samwhispers.wsl.is_wsl", return_value=False),
    ):
        app = SamWhispers(config)
        app.recorder = mock_rec.return_value
        app.whisper = mock_wc.return_value
        app.cleanup = mock_cp.return_value
    # Replace injector and hotkey_listener with mocks after init
    app.injector = MagicMock()
    app.hotkey_listener = MagicMock()
    return app


def test_initial_state_idle() -> None:
    """App starts in IDLE state."""
    app = _make_app()
    assert app._state == State.IDLE


def test_record_start_transitions_to_recording() -> None:
    """on_record_start transitions from IDLE to RECORDING."""
    app = _make_app()
    app._on_record_start()
    assert app._state == State.RECORDING
    app.recorder.start.assert_called_once()


def test_record_start_ignored_when_busy() -> None:
    """on_record_start is ignored when not IDLE."""
    app = _make_app()
    app._state = State.PROCESSING
    app._on_record_start()
    assert app._state == State.PROCESSING
    app.recorder.start.assert_not_called()


def test_record_stop_transitions_to_processing() -> None:
    """on_record_stop transitions from RECORDING to PROCESSING."""
    app = _make_app()
    app._state = State.RECORDING
    app.recorder.stop.return_value = b"fake-wav"
    app._on_record_stop()
    assert app._state == State.PROCESSING
    assert app._work_queue.qsize() == 1


def test_record_stop_ignored_when_not_recording() -> None:
    """on_record_stop is ignored when not RECORDING."""
    app = _make_app()
    app._on_record_stop()
    assert app._state == State.IDLE
    app.recorder.stop.assert_not_called()


def test_process_recording_full_pipeline() -> None:
    """Full pipeline: transcribe -> cleanup -> inject."""
    app = _make_app()
    app.whisper.transcribe.return_value = "hello world"
    app.cleanup.cleanup.return_value = "Hello, world."

    wav_bytes = b"\x00" * 20000
    app._process_recording(wav_bytes)

    app.whisper.transcribe.assert_called_once_with(wav_bytes)
    app.cleanup.cleanup.assert_called_once_with("hello world")
    app.hotkey_listener.suppress.assert_called_once()
    app.injector.inject.assert_called_once_with("Hello, world.")
    app.hotkey_listener.resume.assert_called_once()


def test_process_recording_short_skipped() -> None:
    """Short recording is skipped."""
    app = _make_app()
    app._process_recording(b"tiny")
    app.whisper.transcribe.assert_not_called()


def test_process_recording_empty_transcription_skipped() -> None:
    """Empty transcription is skipped."""
    app = _make_app()
    app.whisper.transcribe.return_value = "   "
    app._process_recording(b"\x00" * 20000)
    app.cleanup.cleanup.assert_not_called()
    app.injector.inject.assert_not_called()


def test_process_loop_handles_exception() -> None:
    """Pipeline error is caught, state returns to IDLE."""
    app = _make_app()
    app.whisper.transcribe.side_effect = RuntimeError("boom")
    app._state = State.PROCESSING
    app._work_queue.put(b"\x00" * 20000)

    app._shutdown_event.clear()

    def run_loop() -> None:
        app._process_loop()

    t = threading.Thread(target=run_loop, daemon=True)
    t.start()

    import time

    time.sleep(0.3)
    app._shutdown_event.set()
    t.join(timeout=2)

    assert app._state == State.IDLE


def test_shutdown_closes_all() -> None:
    """Shutdown closes all components."""
    app = _make_app()
    app.shutdown()
    app.hotkey_listener.stop.assert_called_once()
    app.recorder.close.assert_called_once()
    app.whisper.close.assert_called_once()
    app.cleanup.close.assert_called_once()


def test_concurrent_hotkey_rejected() -> None:
    """Second hotkey press while processing is rejected."""
    app = _make_app()
    app._state = State.PROCESSING
    app._on_record_start()
    assert app._state == State.PROCESSING
    app.recorder.start.assert_not_called()


def test_auto_stop_transitions_to_processing() -> None:
    """on_auto_stop transitions from RECORDING to PROCESSING and enqueues WAV."""
    app = _make_app()
    app._state = State.RECORDING
    wav_bytes = b"\x00" * 20000
    app._on_auto_stop(wav_bytes)
    assert app._state == State.PROCESSING
    assert app._work_queue.qsize() == 1
    assert app._work_queue.get_nowait() == wav_bytes


def test_auto_stop_ignored_when_not_recording() -> None:
    """on_auto_stop is ignored when not in RECORDING state."""
    app = _make_app()
    app._state = State.IDLE
    app._on_auto_stop(b"\x00" * 20000)
    assert app._state == State.IDLE
    assert app._work_queue.qsize() == 0
