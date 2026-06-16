"""Tests for audio capture module."""

from __future__ import annotations

import struct
import wave
import io
from collections import deque

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
    recorder._frames = deque([np.zeros(8000, dtype=np.float32)])  # 0.5s of silence
    recorder._auto_stop()
    assert len(received) == 1
    assert len(received[0]) > 44  # WAV header + PCM data


def test_auto_stop_no_callback_no_error() -> None:
    """_auto_stop without callback does not raise."""
    recorder = AudioRecorder()
    recorder._recording = True
    recorder._frames = deque([np.zeros(8000, dtype=np.float32)])
    recorder._auto_stop()  # Should not raise



# --- Client-side VAD (silence detection) tests ---


def test_silence_detection_fires_auto_stop() -> None:
    """Silence exceeding duration fires on_auto_stop callback."""
    import time

    received: list[bytes] = []
    recorder = AudioRecorder(
        on_auto_stop=lambda b: received.append(b),
        silence_threshold=0.05,
        silence_duration=0.1,  # 100ms for fast test
    )
    # Manually set recording state and add frames
    recorder._recording = True
    recorder._frames = deque([np.zeros(8000, dtype=np.float32)])

    # Simulate silent callbacks
    silent_frame = np.zeros((512, 1), dtype=np.float32)
    recorder._callback(silent_frame, 512, None, None)
    # Wait for silence duration
    time.sleep(0.15)
    recorder._callback(silent_frame, 512, None, None)

    # Wait for Timer(0) to fire
    time.sleep(0.1)
    assert len(received) == 1
    assert len(received[0]) > 44  # WAV header + data


def test_silence_resets_on_loud_frame() -> None:
    """Loud frames reset the silence start time."""
    recorder = AudioRecorder(
        silence_threshold=0.05,
        silence_duration=0.1,
    )
    recorder._recording = True
    recorder._frames = deque([np.zeros(8000, dtype=np.float32)])

    # Silent frame sets _silence_start
    silent_frame = np.zeros((512, 1), dtype=np.float32)
    recorder._callback(silent_frame, 512, None, None)
    assert recorder._silence_start is not None

    # Loud frame resets it
    loud_frame = np.full((512, 1), 0.5, dtype=np.float32)
    recorder._callback(loud_frame, 512, None, None)
    assert recorder._silence_start is None


def test_no_silence_tracking_when_disabled() -> None:
    """No silence tracking when threshold/duration are 0."""
    recorder = AudioRecorder(silence_threshold=0.0, silence_duration=0.0)
    recorder._recording = True
    recorder._frames = deque([np.zeros(8000, dtype=np.float32)])

    silent_frame = np.zeros((512, 1), dtype=np.float32)
    recorder._callback(silent_frame, 512, None, None)
    assert recorder._silence_start is None


def test_vad_fired_prevents_double_stop() -> None:
    """Once _vad_fired is True, silence detection doesn't fire again."""
    import time

    received: list[bytes] = []
    recorder = AudioRecorder(
        on_auto_stop=lambda b: received.append(b),
        silence_threshold=0.05,
        silence_duration=0.05,
    )
    recorder._recording = True
    recorder._frames = deque([np.zeros(8000, dtype=np.float32)])

    silent_frame = np.zeros((512, 1), dtype=np.float32)
    recorder._callback(silent_frame, 512, None, None)
    time.sleep(0.08)
    recorder._callback(silent_frame, 512, None, None)
    time.sleep(0.1)

    # Re-start a recording after VAD fired
    recorder._recording = True
    recorder._frames = deque([np.zeros(8000, dtype=np.float32)])
    recorder._callback(silent_frame, 512, None, None)
    time.sleep(0.08)
    recorder._callback(silent_frame, 512, None, None)
    time.sleep(0.1)

    # First VAD fires, second doesn't because _vad_fired is still True
    assert len(received) == 1



# --- Warm stream (keep_stream_open) tests ---


def test_warm_stream_reuses_existing_stream() -> None:
    """With keep_stream_open=True, stop() does not close the stream and next start() reuses it."""
    from unittest.mock import MagicMock, patch

    mock_stream = MagicMock()
    recorder = AudioRecorder(keep_stream_open=True)

    # Simulate first recording via full open
    with patch("sounddevice.InputStream", return_value=mock_stream):
        recorder.start()
    assert recorder.is_recording()
    recorder._frames = deque([np.zeros(1600, dtype=np.float32)])
    recorder.stop()

    # Stream should NOT have been closed
    mock_stream.close.assert_not_called()
    mock_stream.stop.assert_called_once()

    # Second start should reuse the existing stream (warm restart)
    mock_stream.reset_mock()
    with patch("sounddevice.InputStream") as mock_ctor:
        recorder.start()
        mock_ctor.assert_not_called()  # No new InputStream created
    mock_stream.start.assert_called_once()  # Existing stream restarted
    assert recorder.is_recording()


def test_warm_stream_fallback_on_restart_failure() -> None:
    """If warm restart raises, falls back to full device re-open."""
    from unittest.mock import MagicMock, patch

    old_stream = MagicMock()
    old_stream.start.side_effect = [None, OSError("device lost")]  # first start OK, second fails
    new_stream = MagicMock()

    recorder = AudioRecorder(keep_stream_open=True)

    # First recording
    with patch("sounddevice.InputStream", return_value=old_stream):
        recorder.start()
    recorder._frames = deque([np.zeros(1600, dtype=np.float32)])
    recorder.stop()

    # Second start: warm restart fails, should create new stream
    with patch("sounddevice.InputStream", return_value=new_stream):
        recorder.start()
    assert recorder.is_recording()
    old_stream.close.assert_called()  # Old stream closed on failure
    new_stream.start.assert_called()  # New stream started


def test_keep_stream_open_false_closes_on_stop() -> None:
    """With keep_stream_open=False, stop() closes the stream (original behavior)."""
    from unittest.mock import MagicMock, patch

    mock_stream = MagicMock()
    recorder = AudioRecorder(keep_stream_open=False)

    with patch("sounddevice.InputStream", return_value=mock_stream):
        recorder.start()
    recorder._frames = deque([np.zeros(1600, dtype=np.float32)])
    recorder.stop()

    mock_stream.stop.assert_called_once()
    mock_stream.close.assert_called_once()


def test_close_always_closes_stream() -> None:
    """close() releases the stream even when keep_stream_open=True."""
    from unittest.mock import MagicMock, patch

    mock_stream = MagicMock()
    recorder = AudioRecorder(keep_stream_open=True)

    with patch("sounddevice.InputStream", return_value=mock_stream):
        recorder.start()
    recorder._frames = deque([np.zeros(1600, dtype=np.float32)])
    recorder.stop()

    # Stream still alive after stop
    mock_stream.close.assert_not_called()

    # close() forces it shut
    recorder.close()
    mock_stream.close.assert_called_once()


def test_closed_flag_prevents_start() -> None:
    """After close(), start() is a no-op."""
    from unittest.mock import patch

    recorder = AudioRecorder(keep_stream_open=True)
    recorder.close()

    with patch("sounddevice.InputStream") as mock_ctor:
        recorder.start()
        mock_ctor.assert_not_called()
    assert not recorder.is_recording()



def test_concurrent_start_stop_no_crash() -> None:
    """Concurrent start() and stop() from different threads don't crash."""
    import threading as th
    from unittest.mock import MagicMock, patch

    mock_stream = MagicMock()
    recorder = AudioRecorder(keep_stream_open=True)

    with patch("sounddevice.InputStream", return_value=mock_stream):
        recorder.start()

    errors: list[Exception] = []

    def stop_loop() -> None:
        for _ in range(20):
            try:
                recorder.stop()
            except Exception as e:
                errors.append(e)

    def start_loop() -> None:
        for _ in range(20):
            try:
                recorder.start()
            except Exception as e:
                errors.append(e)

    t1 = th.Thread(target=stop_loop)
    t2 = th.Thread(target=start_loop)
    t1.start()
    t2.start()
    t1.join(timeout=5)
    t2.join(timeout=5)

    assert not errors, f"Concurrent start/stop raised: {errors}"
    recorder.close()



# --- trim_front tests ---


def test_trim_front_full_frames() -> None:
    """trim_front removes complete frames at boundaries."""
    recorder = AudioRecorder()
    recorder._recording = True
    from collections import deque
    recorder._frames = deque([
        np.ones(100, dtype=np.float32),
        np.ones(200, dtype=np.float32),
        np.ones(300, dtype=np.float32),
    ])
    result = recorder.trim_front(300)  # first two frames exactly
    assert result == 300
    assert len(recorder._frames) == 1
    assert recorder._frames[0].size == 300


def test_trim_front_partial_frame() -> None:
    """trim_front slices a frame when n_samples falls mid-frame."""
    recorder = AudioRecorder()
    recorder._recording = True
    from collections import deque
    recorder._frames = deque([np.arange(100, dtype=np.float32)])
    result = recorder.trim_front(30)
    assert result == 30
    assert len(recorder._frames) == 1
    assert recorder._frames[0].size == 70
    assert recorder._frames[0][0] == 30.0  # first remaining sample


def test_trim_front_zero() -> None:
    """trim_front(0) returns 0 and does nothing."""
    recorder = AudioRecorder()
    recorder._recording = True
    from collections import deque
    recorder._frames = deque([np.ones(100, dtype=np.float32)])
    result = recorder.trim_front(0)
    assert result == 0
    assert recorder._frames[0].size == 100


def test_trim_front_more_than_available() -> None:
    """trim_front with n > buffer size returns actual samples available."""
    recorder = AudioRecorder()
    recorder._recording = True
    from collections import deque
    recorder._frames = deque([
        np.ones(50, dtype=np.float32),
        np.ones(50, dtype=np.float32),
    ])
    result = recorder.trim_front(999)
    assert result == 100
    assert len(recorder._frames) == 0


def test_trim_front_not_recording() -> None:
    """trim_front returns 0 when not recording."""
    recorder = AudioRecorder()
    recorder._recording = False
    from collections import deque
    recorder._frames = deque([np.ones(100, dtype=np.float32)])
    result = recorder.trim_front(50)
    assert result == 0
    assert recorder._frames[0].size == 100  # unchanged


def test_snapshot_after_trim() -> None:
    """snapshot reflects buffer state after trim_front."""
    recorder = AudioRecorder()
    recorder._recording = True
    from collections import deque
    recorder._frames = deque([
        np.ones(100, dtype=np.float32) * 1.0,
        np.ones(100, dtype=np.float32) * 2.0,
    ])
    recorder.trim_front(100)  # remove first frame
    snap = recorder.snapshot()
    assert snap.size == 100
    assert np.all(snap == 2.0)
