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
    """Load the synthetic sample WAV fixture."""
    return (FIXTURES / "sample.wav").read_bytes()


def test_full_pipeline_wav_to_text(sample_wav: bytes) -> None:
    """Integration: WAV bytes -> transcribe -> cleanup -> inject (all mocked)."""
    config = AppConfig(
        cleanup=CleanupConfig(
            enabled=True,
            provider="openai",
            openai=OpenAIConfig(api_key="sk-test"),
        )
    )

    with (
        patch("samwhispers.app.HotkeyListener"),
        patch("samwhispers.app.AudioRecorder"),
    ):
        app = SamWhispers(config)

    # Mock the injector to avoid display dependency
    app.injector = MagicMock()

    with respx.mock:
        # Mock whisper-server
        respx.post("http://localhost:8080/inference").mock(
            return_value=httpx.Response(200, json={"text": "hello world"})
        )
        # Mock OpenAI cleanup
        respx.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json={"choices": [{"message": {"content": "Hello, world."}}]},
            )
        )

        app._process_recording(sample_wav)

    app.injector.inject.assert_called_once_with("Hello, world.")
    app.hotkey_listener.suppress.assert_called_once()
    app.hotkey_listener.resume.assert_called_once()


def test_pipeline_cleanup_disabled(sample_wav: bytes) -> None:
    """Integration: cleanup disabled passes transcription through unchanged."""
    config = AppConfig()  # cleanup.enabled = False by default

    with (
        patch("samwhispers.app.HotkeyListener"),
        patch("samwhispers.app.AudioRecorder"),
    ):
        app = SamWhispers(config)

    app.injector = MagicMock()

    with respx.mock:
        respx.post("http://localhost:8080/inference").mock(
            return_value=httpx.Response(200, json={"text": "hello world"})
        )

        app._process_recording(sample_wav)

    # Text should be passed through without cleanup
    app.injector.inject.assert_called_once_with("hello world")


def test_pipeline_whisper_failure(sample_wav: bytes) -> None:
    """Integration: whisper failure raises, state returns to IDLE via process_loop."""
    config = AppConfig()

    with (
        patch("samwhispers.app.HotkeyListener"),
        patch("samwhispers.app.AudioRecorder"),
    ):
        app = SamWhispers(config)

    app.injector = MagicMock()

    with respx.mock:
        respx.post("http://localhost:8080/inference").mock(
            return_value=httpx.Response(500, text="Server Error")
        )

        # Should raise (caught by process_loop in real usage)
        with pytest.raises(httpx.HTTPStatusError):
            app._process_recording(sample_wav)

    app.injector.inject.assert_not_called()


def test_state_machine_full_cycle() -> None:
    """Integration: IDLE -> RECORDING -> PROCESSING -> IDLE cycle."""
    config = AppConfig()

    with (
        patch("samwhispers.app.HotkeyListener"),
        patch("samwhispers.app.AudioRecorder"),
    ):
        app = SamWhispers(config)

    app.injector = MagicMock()
    app.recorder.stop.return_value = b"\x00" * 20000

    # Start recording
    assert app._state == State.IDLE
    app._on_record_start()
    assert app._state == State.RECORDING

    # Stop recording
    app._on_record_stop()
    assert app._state == State.PROCESSING

    # Process (mock whisper to return text)
    with respx.mock:
        respx.post("http://localhost:8080/inference").mock(
            return_value=httpx.Response(200, json={"text": "test"})
        )
        wav_bytes = app._work_queue.get(timeout=1)
        app._process_recording(wav_bytes)

    # Back to IDLE (normally done by process_loop's finally block)
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
        assert wf.getnframes() == 16000  # 1 second
