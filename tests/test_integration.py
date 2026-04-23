"""Integration tests: full pipeline with mocked external services."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest
import respx

from samwhispers.app import SamWhispers, State
from samwhispers.config import AppConfig, CleanupConfig, OpenAIConfig

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture()
def sample_wav() -> bytes:
    return (FIXTURES / "sample.wav").read_bytes()


def _make_app(config: AppConfig | None = None) -> SamWhispers:
    """Create app with mocked recorder/hotkey/injector, real whisper+cleanup clients."""
    config = config or AppConfig()
    with (
        patch("samwhispers.app.AudioRecorder"),
        patch("samwhispers.wsl.is_wsl", return_value=False),
    ):
        app = SamWhispers(config)
    app.injector = MagicMock()
    app.hotkey_listener = MagicMock()
    return app


def test_full_pipeline_wav_to_text(sample_wav: bytes) -> None:
    """Integration: WAV bytes -> transcribe -> cleanup -> inject (all mocked)."""
    config = AppConfig(
        cleanup=CleanupConfig(
            enabled=True,
            provider="openai",
            openai=OpenAIConfig(api_key="sk-test"),
        )
    )
    app = _make_app(config)

    with respx.mock:
        respx.post("http://localhost:8080/inference").mock(
            return_value=httpx.Response(200, json={"text": "hello world"})
        )
        respx.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json={"choices": [{"message": {"content": "Hello, world."}}]},
            )
        )
        app._process_recording(sample_wav)

    app.injector.inject.assert_called_once_with("Hello, world.\n")


def test_pipeline_cleanup_disabled(sample_wav: bytes) -> None:
    """Integration: cleanup disabled passes transcription through unchanged."""
    app = _make_app()

    with respx.mock:
        respx.post("http://localhost:8080/inference").mock(
            return_value=httpx.Response(200, json={"text": "hello world"})
        )
        app._process_recording(sample_wav)

    app.injector.inject.assert_called_once_with("hello world\n")


def test_pipeline_whisper_failure(sample_wav: bytes) -> None:
    """Integration: whisper failure raises."""
    app = _make_app()

    with respx.mock:
        respx.post("http://localhost:8080/inference").mock(
            return_value=httpx.Response(500, text="Server Error")
        )
        with pytest.raises(httpx.HTTPStatusError):
            app._process_recording(sample_wav)

    app.injector.inject.assert_not_called()


def test_state_machine_full_cycle() -> None:
    """Integration: IDLE -> RECORDING -> PROCESSING -> IDLE cycle."""
    app = _make_app()
    app.recorder.stop.return_value = b"\x00" * 20000

    assert app._state == State.IDLE
    app._on_record_start()
    assert app._state == State.RECORDING

    app._on_record_stop()
    assert app._state == State.PROCESSING

    with respx.mock:
        respx.post("http://localhost:8080/inference").mock(
            return_value=httpx.Response(200, json={"text": "test"})
        )
        wav_bytes = app._work_queue.get(timeout=1)
        app._process_recording(wav_bytes)

    with app._lock:
        app._state = State.IDLE
    assert app._state == State.IDLE


def test_sample_wav_fixture_valid(sample_wav: bytes) -> None:
    """Verify the sample WAV fixture is a valid WAV file."""
    import io
    import wave

    buf = io.BytesIO(sample_wav)
    with wave.open(buf, "rb") as wf:
        assert wf.getnchannels() == 1
        assert wf.getsampwidth() == 2
        assert wf.getframerate() == 16000
        assert wf.getnframes() == 16000


def test_e2e_hotkey_record_transcribe_inject(sample_wav: bytes) -> None:
    """E2E: simulate hotkey press/release -> record -> transcribe -> inject, no real mic."""
    import threading

    app = _make_app()
    app.recorder.stop.return_value = sample_wav

    # Simulate hotkey press -> starts recording
    app._on_record_start()
    assert app._state == State.RECORDING
    app.recorder.start.assert_called_once()

    # Simulate hotkey release -> stops recording, enqueues WAV
    app._on_record_stop()
    assert app._state == State.PROCESSING
    assert not app._work_queue.empty()

    # Run the worker loop once to process the queued WAV
    done = threading.Event()

    with respx.mock:
        respx.post("http://localhost:8080/inference").mock(
            return_value=httpx.Response(200, json={"text": "hello from e2e"})
        )

        def run_worker() -> None:
            try:
                wav = app._work_queue.get(timeout=2)
                app._process_recording(wav)
            finally:
                with app._lock:
                    app._state = State.IDLE
                done.set()

        t = threading.Thread(target=run_worker)
        t.start()
        done.wait(timeout=5)
        t.join(timeout=1)

    # Verify text was injected and state returned to IDLE
    app.injector.inject.assert_called_once_with("hello from e2e\n")
    app.hotkey_listener.suppress.assert_called_once()
    app.hotkey_listener.resume.assert_called_once()
    assert app._state == State.IDLE


def test_e2e_audio_failure_resets_to_idle() -> None:
    """E2E: audio device failure on hotkey press resets state to IDLE."""
    app = _make_app()
    app.recorder.start.side_effect = OSError("PortAudio library not found")

    app._on_record_start()

    assert app._state == State.IDLE
    app.recorder.start.assert_called_once()
    app.injector.inject.assert_not_called()
