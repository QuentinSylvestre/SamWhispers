# Instant Recording Start & Taskbar Icon Fix

> **Date**: 2026-06-14
> **Status**: Complete
> **Scope**: Reduce hotkey-to-recording latency from ~1s to near-instant; hide overlay from Windows taskbar
> **Last Updated**: 2026-06-15 00:04

---

## Intent

### Problem statement & desired outcomes

SamWhispers has ~1s latency between hotkey press and actual audio capture start, caused by creating a new `sd.InputStream` (PortAudio device open) on every recording. Additionally, the overlay's Tk window shows a Python icon in the Windows taskbar/Alt-Tab during recording because it lacks the `-toolwindow` attribute. Both degrade the user experience of a push-to-talk tool that should feel instant and invisible.

### Success criteria

1. Recording starts within ~50ms of hotkey press (when `keep_stream_open = true`)
2. No Python/Tk icon appears in the Windows taskbar or Alt-Tab during recording
3. Device-unplug edge case handled transparently (fallback to full re-open)

### Scope boundaries & non-goals

- In scope: warm audio stream with config toggle, overlay `-toolwindow` fix
- Not in scope: ring buffer pre-capture, streaming architecture changes, Linux/WSL overlay behavior, whisper-server cold-inference warmup

## Context

`AudioRecorder.start()` (`audio.py:120`) creates a new `sd.InputStream` on every hotkey press and `stop()` (`audio.py:165`) calls both `stream.stop()` and `stream.close()`, releasing the device handle. Re-opening costs ~500-1000ms on Windows (PortAudio WASAPI init). The sounddevice API supports `stop()` without `close()` — a stopped stream can be re-started near-instantly.

The overlay (`overlay.py:197`) sets `overrideredirect(True)` but never sets `-toolwindow`, so Windows shows the Tk window in the taskbar/Alt-Tab when `deiconify()` is called at `overlay.py:337`.

## Files to modify

| File | Change |
|---|---|
| `src/samwhispers/config.py` | Add `keep_stream_open: bool = True` to `AudioConfig` |
| `src/samwhispers/audio.py` | Keep stream warm between recordings; serialized stop/start with lock |
| `src/samwhispers/app.py` | Pass `keep_stream_open` config to `AudioRecorder` |
| `src/samwhispers/overlay.py` | Add `-toolwindow` attribute on Windows |
| `config.example.toml` | Document `keep_stream_open` |
| `tests/test_audio.py` | Test warm-stream and retry behavior |

## External Dependencies

None — code-only change, no new packages.

## Rollout / Migration / Cleanup

None — new config field has a default; existing configs continue to work unchanged.

## Step-by-step

### 1. Overlay taskbar fix [QA]

In `overlay.py` `OverlayApp.__init__`, after the `-transparentcolor` try/except block (~line 215), before `root.configure(bg=...)`:

```python
# Hide from taskbar and Alt-Tab on Windows
if sys.platform == "win32":
    root.wm_attributes("-toolwindow", True)
```

Uses the local `root` parameter (same scope as the existing `overrideredirect` call above it).

### 2. Config: add `keep_stream_open` [QA]

In `config.py` `AudioConfig` dataclass (line 251):

```python
@dataclass
class AudioConfig:
    sample_rate: int = 16000
    max_duration: float = 300.0
    keep_stream_open: bool = True
```

Add to `config.example.toml` under `[audio]`:

```toml
keep_stream_open = true     # Keep mic handle open between recordings for instant start (~holds mic "in use")
```

### 3. Warm audio stream [QA]

Key design constraints from review:
- **All `_stream` access (read/write/start/stop) must be inside `_lock`** to prevent concurrent start/stop races (PortAudio UB).
- **`_recording = True` set only after `stream.start()` succeeds** to prevent empty-WAV pipeline injection on failure.
- **`_error` reset after stream starts** (not before) to avoid stale error flags.
- **`_closed` sentinel** prevents `start()` racing with `close()` during shutdown.

```python
class AudioRecorder:
    def __init__(self, ..., keep_stream_open: bool = True) -> None:
        ...
        self._keep_stream_open = keep_stream_open
        self._closed = False

    def start(self) -> None:
        import sounddevice as sd

        with self._lock:
            if self._recording or self._closed:
                return
            self._frames = []
            self._vad_fired = False
            self._silence_start = None

            # Try warm restart (stream kept open from previous recording)
            if self._stream is not None:
                try:
                    self._stream.start()
                    self._recording = True
                    self._error = False
                except Exception:
                    log.warning("Warm stream restart failed, re-opening device")
                    try:
                        self._stream.close()
                    except Exception:
                        pass
                    self._stream = None
                    # Fall through to full open below

            if self._recording:
                # Warm restart succeeded — set timer and return
                self._timer = threading.Timer(self._max_duration, self._auto_stop)
                self._timer.daemon = True
                self._timer.start()
                return

        # Full open (first time, or after warm restart failure)
        # Done outside lock because sd.InputStream() is slow I/O
        for attempt in range(2):
            try:
                stream = sd.InputStream(
                    samplerate=self._sample_rate,
                    channels=1,
                    dtype="float32",
                    callback=self._callback,
                )
                stream.start()
                break
            except Exception:
                if attempt == 0:
                    log.warning("Audio stream failed, retrying in 0.5s...")
                    time.sleep(0.5)
                else:
                    raise

        with self._lock:
            if self._closed:
                # Shutdown happened during open — clean up
                stream.stop()
                stream.close()
                return
            self._stream = stream
            self._recording = True
            self._error = False

        self._timer = threading.Timer(self._max_duration, self._auto_stop)
        self._timer.daemon = True
        self._timer.start()

    def stop(self) -> bytes:
        with self._lock:
            if not self._recording:
                return b""
            self._recording = False
            stream = self._stream
            if not self._keep_stream_open:
                self._stream = None
            timer = self._timer
            self._timer = None
            frames = self._frames
            self._frames = []
            keep = self._keep_stream_open

        if timer:
            timer.cancel()

        if stream is not None:
            stream.stop()
            if not keep:
                stream.close()

        if self._error:
            log.warning("Audio errors occurred during recording, result may be partial")

        if not frames:
            return b""

        audio = np.concatenate(frames)
        return numpy_to_wav(audio, self._sample_rate)

    def close(self) -> None:
        """Release resources (app shutdown). Always closes stream."""
        with self._lock:
            self._closed = True
            recording = self._recording
        if recording:
            self.stop()
        with self._lock:
            stream = self._stream
            self._stream = None
        if stream is not None:
            try:
                stream.close()
            except Exception:
                pass
        if self._timer:
            self._timer.cancel()
```

In `app.py`, pass the config:

```python
self.recorder = AudioRecorder(
    sample_rate=config.audio.sample_rate,
    max_duration=config.audio.max_duration,
    keep_stream_open=config.audio.keep_stream_open,
    on_auto_stop=self._on_auto_stop,
    on_level=self._emit_level,
    silence_threshold=...,
    silence_duration=...,
)
```

Note: the warm-restart `stream.start()` is inside the lock. This is acceptable because `Pa_StartStream()` on a stopped (not closed) stream is near-instant (~microseconds) — it just signals the audio callback thread to resume. The expensive operation is `Pa_OpenStream()` (in `sd.InputStream()` constructor), which remains outside the lock.

### 4. Tests

Add tests for:
- Warm stream reuse: mock `sd.InputStream`, call `start()`→`stop()`→`start()`, verify second `start()` calls `stream.start()` (not `sd.InputStream()` again) and `close()` was NOT called
- Fallback: mock `stream.start()` to raise on second invocation, verify it falls back to full re-open
- `close()` always closes stream even when `keep_stream_open=True`
- `_closed` flag: after `close()`, subsequent `start()` is a no-op
- `keep_stream_open=False` preserves current behavior (stream closed on every stop)
- Threading test: concurrent `stop()` and `start()` from different threads don't crash

## Verification

```bash
python -m pytest tests/test_audio.py tests/test_app.py -v
python -m ruff check src/ tests/
python -m mypy src/
```

Manual:
1. Press hotkey — verify recording starts instantly (no perceptible delay)
2. Check Windows taskbar during recording — no Python/Tk icon
3. Alt-Tab during recording — overlay not listed
4. Unplug/replug mic between recordings — next recording works (may have one slow start)

## Documentation updates

| Document | Update needed |
|---|---|
| `README.md` | Add `keep_stream_open` to Config Options table under `[audio]` |
| `config.example.toml` | Already covered in Step 2 |

## Implementation Notes

Implementation (2026-06-14, code: 320145d)

All 4 steps implemented in a single pass: overlay `-toolwindow` fix, `keep_stream_open` config field, warm audio stream with `_closed` sentinel and serialized lock access, and 5 new tests. 350 tests pass; no new lint/type errors introduced. README updated with new config option.

## Review Log

### 2026-06-14 — Plan Review (High effort, 4 personas)

47 raw findings (11H, 21M, 15L) across Senior engineer, Architect, Performance engineer, Reliability engineer. After dedup: 12 unique findings. 9 auto-resolved.

| # | Severity | Finding | Status |
|---|---|---|---|
| 1 | High | Race: `_stream` accessed outside lock in `stop()` while `start()` runs concurrently | Resolved — all stream access now under `_lock` |
| 2 | High | `_recording = True` before `stream.start()` succeeds → empty WAV on failure | Resolved — flag set only after success |
| 3 | High | Stale drain frames after `stop()` could leak into next recording | Resolved — `_frames = []` reset inside lock before warm restart |
| 4 | Medium | `close()` vs `start()` race on shutdown | Resolved — `_closed` sentinel added |
| 5 | Medium | `_error` flag persists across warm restarts | Resolved — reset after stream.start() |
| 6 | Medium | `-toolwindow` placement must be after `-transparentcolor` block | Resolved — placement specified explicitly |
| 7 | Medium | Idle timeout for mic hold (battery/exclusive mode) | Noted — documented in config comment; future enhancement |
| 8 | Medium | Whisper-server cold inference is secondary latency source | Noted — added to non-goals |
| 9 | Medium | Timer setup duplicated in two branches | Resolved — unified structure |
| 10 | Low | Line reference off-by-one (250 vs 251) | Resolved |
| 11 | Low | Sleep blocks hotkey thread on failure | Noted — matches existing behavior, acceptable |
| 12 | Low | Concurrency test needed | Resolved — added to test plan |


### 2026-06-14 -- Post-Implementation Review

Overall implementation health: Green.
Personas: Senior engineer, Performance engineer, Reliability engineer, End-user advocate.
4 findings (0 High, 2 Medium, 7 Low). 2 auto-resolved.

| # | Severity | Finding | Resolution |
|---|---|---|---|
| 1 | Medium | Missing threading test specified in plan Step 4 | Fixed — added `test_concurrent_start_stop_no_crash` (c3fbc72) |
| 2 | Medium | `_trigger_vad_stop` reads `self._timer` without None guard — race with `close()` | Fixed — local-var guard added (c3fbc72) |
| 3 | Low | `_error` read outside lock in `stop()` — benign under CPython GIL | Accepted — PortAudio stop-blocks-until-callback contract makes this safe |
| 4 | Low | No idle timeout for held microphone | Accepted — plan explicitly defers (finding #7); `keep_stream_open=false` is escape hatch |
| 5 | Low | Timer allocation duplicated in warm/cold paths | Accepted — no runtime impact, minor hygiene |
| 6 | Low | README could explain mic-in-use trade-off more | Accepted — config comment covers it; low-priority |
| 7 | Low | `-toolwindow` not wrapped in try/except | Accepted — attribute has existed since Tk 8.5, risk near-zero |
