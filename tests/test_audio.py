"""Tests for audio capture module."""

from __future__ import annotations

import struct
import wave
import io

import numpy as np

from samwhispers.audio import AudioRecorder, min_wav_size, numpy_to_wav


def test_numpy_to_wav_valid_header() -> None:
    """numpy_to_wav produces valid WAV with correct format."""
    audio = np.zeros(16000, dtype=np.float32)  # 1 second of silence
    wav = numpy_to_wav(audio, 16000)

    buf = io.BytesIO(wav)
    with wave.open(buf, "rb") as wf:
        assert wf.getnchannels() == 1
        assert wf.getsampwidth() == 2
        assert wf.getframerate() == 16000
        assert wf.getnframes() == 16000


def test_numpy_to_wav_clipping() -> None:
    """Values outside [-1, 1] are clipped."""
    audio = np.array([2.0, -2.0, 0.5], dtype=np.float32)
    wav = numpy_to_wav(audio, 16000)

    buf = io.BytesIO(wav)
    with wave.open(buf, "rb") as wf:
        raw = wf.readframes(3)
    samples = struct.unpack("<3h", raw)
    assert samples[0] == 32767  # clipped to max
    assert samples[1] == -32767  # clipped to min (np.clip -1.0 * 32767)
    assert samples[2] == 16383  # 0.5 * 32767 truncated


def test_min_wav_size() -> None:
    """min_wav_size calculates correct threshold."""
    size = min_wav_size(16000, 0.5)
    assert size == 44 + 16000  # 44 header + 16000 * 0.5 * 2


def test_double_stop_returns_empty() -> None:
    """Calling stop() when not recording returns empty bytes."""
    recorder = AudioRecorder()
    assert recorder.stop() == b""
    assert recorder.stop() == b""  # second call also safe


def test_is_recording_default_false() -> None:
    """New recorder is not recording."""
    recorder = AudioRecorder()
    assert recorder.is_recording() is False


def test_auto_stop_invokes_callback_with_wav_bytes() -> None:
    """_auto_stop passes WAV bytes to on_auto_stop callback."""
    received: list[bytes] = []
    recorder = AudioRecorder(on_auto_stop=lambda b: received.append(b))
    # Manually populate frames so stop() returns real WAV data
    recorder._recording = True
    recorder._frames = [np.zeros(8000, dtype=np.float32)]  # 0.5s of silence
    recorder._auto_stop()
    assert len(received) == 1
    assert len(received[0]) > 44  # WAV header + PCM data


def test_auto_stop_no_callback_no_error() -> None:
    """_auto_stop without callback does not raise."""
    recorder = AudioRecorder()
    recorder._recording = True
    recorder._frames = [np.zeros(8000, dtype=np.float32)]
    recorder._auto_stop()  # Should not raise
