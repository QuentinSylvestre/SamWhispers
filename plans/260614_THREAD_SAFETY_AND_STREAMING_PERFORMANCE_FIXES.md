# Thread Safety and Streaming Performance Fixes

> **Date**: 2026-06-14
> **Status**: Draft  <!-- Status lifecycle: Exploring → Draft → In Progress → Complete -->
> **Scope**: Fix 5 higher-effort code review findings: recording race (H3/M12), supervisor thread-safety (M1-M3), tray thread dispatch (M18), streaming O(n^2) (M20)

---

## Intent

Fix the remaining thread-safety bugs and performance issue identified in the 2026-06-14 code review and deferred to the roadmap. These are the findings that required design thought before implementation. All design decisions have been resolved via `/qexplore`.

Invariants:
- `AudioRecorder.stop()` must be idempotent — calling it twice returns `b""` on the second call without raising or double-closing the stream.
- `_finalize_streaming` must be idempotent — a second concurrent call returns immediately without spawning a duplicate worker thread.
- Streaming per-tick CPU cost must be bounded by a constant regardless of total recording duration.

## Context

The code review found race conditions in the recording lifecycle (audio.py, app.py), three independent thread-safety issues in the supervisor, an unsafe cross-thread tray update on Linux, and O(n^2) streaming cost. All decisions were settled during `/qexplore`: capture-under-lock for audio, lock-protected session grab, json serialization for relaunch args, GLib.idle_add for Linux tray, 30s sliding window for streaming.

## Files to modify

| File | Change |
|---|---|
| `src/samwhispers/audio.py` | Make `stop()` idempotent via capture-under-lock pattern |
| `src/samwhispers/app.py` | Guard `_finalize_streaming` with lock on session grab |
| `src/samwhispers/supervisor.py` | M1: lock proc read in log reader; M2: json serialize relaunch args; M3: log warning on join timeout |
| `src/samwhispers/tray.py` | Dispatch icon updates via GLib.idle_add on Linux |
| `src/samwhispers/streaming.py` | Add sliding window to ChunkedEngine |
| `src/samwhispers/config.py` | Add `window_seconds` field to StreamingConfig |
| `tests/test_audio.py` | Test stop() idempotency |
| `tests/test_streaming.py` | Test sliding window behavior |

## External Dependencies

None

## Rollout / Migration / Cleanup

None — all changes are backward-compatible. The new `streaming.window_seconds` config field defaults to 30 (existing behavior is "no window" = full buffer, so this is a behavior change for streaming users but strictly an improvement).

## Step-by-step

### 1. Make `AudioRecorder.stop()` idempotent (H3/M12) [QA]

In `audio.py`, change `stop()` to capture `_stream` and `_timer` under the lock alongside `_recording = False`, nil the instance fields, then operate on the local references outside the lock:

```python
def stop(self) -> bytes:
    """Stop recording and return WAV bytes. Returns b'' if not recording."""
    with self._lock:
        if not self._recording:
            return b""
        self._recording = False
        stream = self._stream
        self._stream = None
        timer = self._timer
        self._timer = None
        frames = self._frames
        self._frames = []

    if timer:
        timer.cancel()

    if stream is not None:
        stream.stop()
        stream.close()

    if self._error:
        log.warning("Audio errors occurred during recording, result may be partial")

    if not frames:
        return b""

    audio = np.concatenate(frames)
    wav = numpy_to_wav(audio, self._sample_rate)
    log.debug("Recording stopped: %.1fs, %d bytes", len(audio) / self._sample_rate, len(wav))
    return wav
```

Also update `close()` to not call `stop()` redundantly if `_stream` is already None.

### 2. Guard `_finalize_streaming` in app.py (H3) [QA]

Protect the session grab with `self._lock`:

```python
def _finalize_streaming(self, from_auto_stop: bool, wav_bytes: bytes = b"") -> None:
    from samwhispers.audio import wav_to_float32

    self._stream_stop.set()
    if self._stream_thread is not None:
        self._stream_thread.join(timeout=5.0)

    with self._lock:
        session = self._stream_session
        self._stream_session = None
    if session is None:
        # Already finalized by a concurrent call
        return

    if from_auto_stop:
        final_audio = wav_to_float32(wav_bytes)
    else:
        final_audio = self.recorder.snapshot()
        self.recorder.stop()

    t = threading.Thread(
        target=self._finalize_stream_worker,
        args=(session, final_audio),
        daemon=True,
        name="stream-finalize",
    )
    self._finalize_thread = t
    t.start()
```

### 3. Supervisor thread-safety fixes (M1, M2, M3)

**M1** — In `_read_worker_logs`, read `self._proc` under lock:

```python
def _read_worker_logs(self) -> None:
    with self._lock:
        proc = self._proc
    if proc is None or proc.stderr is None:
        return
    for line in proc.stderr:
        # ... (unchanged)
```

**M2** — In `_relaunch_detached`, serialize args via JSON instead of repr:

```python
import json
# ...
args_json = json.dumps(extra_args)
cmd = [_python_launcher(), "-c",
       f"import sys, json; sys.argv = ['samwhispers-supervisor'] + json.loads('{args_json}'); "
       "from samwhispers.supervisor import main; main()"]
```

**M3** — After `join(timeout=3.0)` in `shutdown()`, log if thread is still alive:

```python
if self._monitor_thread and self._monitor_thread.is_alive():
    self._monitor_thread.join(timeout=3.0)
    if self._monitor_thread.is_alive():
        log.warning("Monitor thread did not exit within 3s (will be cleaned up at process exit)")
```

### 4. Tray icon thread-safe dispatch (M18) [QA]

In `tray.py`, wrap the `on_state_change` callback body with a platform check:

```python
def on_state_change(state: WorkerState) -> None:
    def _update() -> None:
        try:
            icon.icon = _make_image(state)
            icon.title = f"SamWhispers ({state.value})"
            icon.update_menu()
        except Exception:
            log.debug("Failed to update tray icon", exc_info=True)

    if sys.platform == "linux":
        try:
            from gi.repository import GLib  # type: ignore[import-untyped]
            GLib.idle_add(_update)
            return
        except ImportError:
            pass
    _update()
```

Add `import sys` at the top of tray.py.

### 5. Streaming sliding window (M20) [QA]

**config.py**: Add `window_seconds` to `StreamingConfig`:

```python
@dataclass
class StreamingConfig:
    enabled: bool = False
    engine: str = "chunked"
    # ... existing fields ...
    window_seconds: float = 30.0
```

**streaming.py**: Modify `StreamingSession.tick()` to trim audio to the window:

```python
def tick(self, audio: np.ndarray) -> str:
    """Decode the current audio (windowed), stabilize, emit updates."""
    # Apply sliding window: only decode the last window_seconds of audio
    max_samples = int(self._window_seconds * self._sample_rate)
    if audio.size > max_samples:
        audio = audio[-max_samples:]
    words = split_words(self._engine.transcribe(audio, self._sample_rate))
    # ... rest unchanged
```

Pass `window_seconds` from config to `StreamingSession.__init__` in `app.py:_start_stream`.

## Verification

- `python -m pytest tests/ -v` — all existing tests pass
- `python -m pytest tests/test_audio.py -k idempotent` — new test for double-stop
- `python -m pytest tests/test_streaming.py -k window` — new test for windowed transcription
- `python -m ruff check src/ tests/` — lint clean
- Manual: run with streaming enabled, record >30s, verify CPU stays bounded

## Documentation updates

- Update `config.example.toml` with `window_seconds = 30.0` in `[streaming]` section
- Update `README.md` streaming section to mention the window config
- Remove H3, M1-M3, M12, M18, M20 entries from `plans/ROADMAP.md` "Code Review — Higher-Effort Fixes" section
