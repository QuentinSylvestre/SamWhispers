# SamWhispers v1 -- Local Voice-to-Text Daemon

> **Date**: 2026-03-19
> **Status**: Draft
> **Scope**: Cross-platform push-to-talk voice dictation daemon using whisper.cpp
> **Estimated effort**: 5-7 days

---

## 1) Goal

Build a headless daemon that captures microphone audio on a global hotkey, transcribes it locally via whisper-server (whisper.cpp), optionally cleans up the text via OpenAI or Anthropic APIs, and pastes the result into the active application via clipboard.

Runs natively on Linux (X11) and Windows. No cloud dependency for core transcription.

## 2) Current State

Greenfield project. Empty repository at `SamWhispers/`.

### External dependencies (pre-existing)

- **whisper.cpp**: User must build `whisper-server` and download a model separately. The README will document this.
- **PortAudio**: Required by `sounddevice`. Linux: `sudo apt install libportaudio2`. Windows: bundled with the pip package.
- **xclip**: Required on Linux for clipboard. `sudo apt install xclip`.
- **X11**: Required on Linux for global hotkeys via pynput. Wayland is not supported in v1.

## 3) Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| whisper.cpp integration | whisper-server HTTP API (`/inference` endpoint) | Model loaded once, fast per-request, clean separation |
| AI cleanup HTTP | Raw `httpx` (no SDKs) | Minimal dependencies, full control, both APIs are simple REST calls |
| Hotkey library | `pynput` | Cross-platform (Linux X11 + Windows), handles both listening and key simulation |
| Clipboard | `pyperclip` | Cross-platform abstraction over xclip/pbcopy/win32 |
| Config format | TOML via `tomllib` (stdlib) | Built into Python 3.11+, no extra dependency for reading |
| Audio format | 16kHz mono 16-bit PCM WAV | Required by whisper.cpp |
| Push-to-talk modes | Hold (default) + Toggle | Hold is natural for short dictation, toggle for longer passages |
| Project layout | `src/` layout | Best practice for packaging, avoids import confusion |
| Minimum Python | 3.11 | `tomllib` in stdlib, modern typing features |
| Concurrency model | State machine + worker thread | Hotkey callbacks must return fast; transcription pipeline runs on a dedicated worker thread via queue |
| HTTP clients | Persistent `httpx.Client` with explicit timeouts | Connection pooling, no hung requests |

<!-- resolves review finding: Implementability #2 (wrong endpoint), #4 (sync callback), Reliability #1-4 -->

## 4) External Dependencies & Costs

### Required external changes

| Category | Change needed | Owner | Status |
|---|---|---|---|
| Secrets / Env vars | OpenAI/Anthropic API keys (optional, only for cleanup) | User | Pending |

### Cost impact

AI cleanup (when enabled) incurs API costs per transcription. With `gpt-4o-mini` or `claude-sonnet-4-20250514`, typical cost is <$0.01 per cleanup call. User must opt in via config. No cost when cleanup is disabled (default).

## 5) Implementation Phases

---

### Phase 1: Project Scaffolding

**Goal**: Establish project structure, dependencies, tooling, and dev workflow.

`pyproject.toml` (key sections):
```toml
[build-system]
requires = ["setuptools>=68.0"]
build-backend = "setuptools.build_meta"

[project]
name = "samwhispers"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "sounddevice>=0.4",
    "numpy>=1.24",
    "pynput>=1.7",
    "httpx>=0.27",
    "pyperclip>=1.8",
]

[project.optional-dependencies]
dev = ["pytest>=8.0", "respx>=0.21", "mypy>=1.8", "ruff>=0.3"]

[project.scripts]
samwhispers = "samwhispers.__main__:main"

[tool.setuptools.packages.find]
where = ["src"]

[tool.ruff]
target-version = "py311"
line-length = 100

[tool.mypy]
python_version = "3.11"
strict = true
```

`Makefile`, `config.example.toml`, and skeleton files for all modules (empty with docstrings):
- `src/samwhispers/{__init__,__main__,app,audio,config,cleanup,hotkeys,inject,transcribe}.py`
- `tests/{__init__,conftest}.py`

**Exit criteria**:
- [ ] `make setup` succeeds
- [ ] `make check` passes (empty tests OK)
- [ ] `python -m samwhispers` runs without import errors (can exit immediately)

**Commit**: `feat: scaffold project structure and dependencies`

---

### Phase 2: Configuration Module

**Goal**: Load, validate, and provide typed access to TOML config.

**File**: `src/samwhispers/config.py`

Dataclasses for each config section (`HotkeyConfig`, `WhisperConfig`, `CleanupConfig` with nested `OpenAIConfig`/`AnthropicConfig`, `AudioConfig`, `AppConfig`). Key details:
- `load_config(path: Path | str | None = None) -> AppConfig` -- loads TOML, merges with defaults
- `find_config() -> Path | None` -- searches CWD then `~/.config/samwhispers/`
- Validates: mode is "hold"/"toggle", provider is "openai"/"anthropic"
- Warns if cleanup enabled but API key is empty

**Tests** (`tests/test_config.py`): valid TOML, missing file (defaults), partial config, invalid values, cleanup-without-key warning.

**Exit criteria**:
- [ ] `pytest tests/test_config.py -v` -- all pass
- [ ] Config loads from file, falls back to defaults, validates

**Commit**: `feat(config): TOML config loading with validation and defaults`

---

### Phase 3: Audio Capture Module

**Goal**: Record microphone audio and produce 16kHz mono 16-bit PCM WAV bytes.

**File**: `src/samwhispers/audio.py`

```python
class AudioRecorder:
    def __init__(self, sample_rate: int = 16000, max_duration: float = 300.0): ...
    def start(self) -> None: ...       # Opens sd.InputStream, appends frames via callback
    def stop(self) -> bytes: ...        # Stops stream, returns WAV bytes (or b"" if not recording)
    def is_recording(self) -> bool: ...
    def close(self) -> None: ...        # Release resources

def numpy_to_wav(audio: np.ndarray, sample_rate: int) -> bytes: ...
```

Key implementation details:
- `threading.Lock` guards `_recording` flag and `_frames` list (callback runs on audio thread)
- `stop()` returns `b""` if not currently recording (double-stop guard)
- `threading.Timer` auto-stops after `max_duration` seconds with a log warning
- InputStream error callback sets an error flag; `stop()` logs partial audio warning
- Minimum duration check: `0.5s` = `sample_rate * 0.5 * 2 + 44` bytes threshold

**Tests** (`tests/test_audio.py`): `numpy_to_wav` with synthetic data -> valid WAV header/format, minimum duration check, double-stop safety.

**Exit criteria**:
- [ ] `pytest tests/test_audio.py -v` -- all pass
- [ ] WAV output verified: 16kHz, mono, 16-bit PCM

**Commit**: `feat(audio): microphone capture with WAV encoding`

---

### Phase 4: Whisper Server Client

**Goal**: POST audio to whisper-server's `/inference` endpoint and extract transcription text.

**File**: `src/samwhispers/transcribe.py`

```python
class WhisperClient:
    def __init__(self, server_url: str, language: str = "en"):
        self._client = httpx.Client(
            base_url=server_url.rstrip("/"),
            timeout=httpx.Timeout(connect=5.0, read=120.0, write=10.0),
        )
    def transcribe(self, wav_bytes: bytes) -> str: ...  # POST /inference, multipart form
    def health_check(self) -> bool: ...                  # GET / -> 200 means alive
    def close(self) -> None: ...                         # Close httpx client
```

whisper-server `/inference` accepts multipart form: `file` (WAV), `temperature` (0.0), `response_format` ("json"), `language`. Response: `{"text": "..."}`.

Health check: `GET /` returns 200 with HTML when server is ready.

Simple retry: 1 retry with 1s backoff on 5xx or connection errors. No retry on 4xx.

**Tests** (`tests/test_transcribe.py`, using `respx`): successful transcription, server error, unreachable server, empty response, retry on 503.

**Exit criteria**:
- [ ] `pytest tests/test_transcribe.py -v` -- all pass
- [ ] Request format matches whisper-server's actual `/inference` API

**Commit**: `feat(transcribe): whisper-server HTTP client with retry and timeouts`

---

### Phase 5: AI Cleanup Module

**Goal**: Optional text cleanup via OpenAI or Anthropic APIs.

**File**: `src/samwhispers/cleanup.py`

```python
class CleanupProvider:
    def __init__(self, config: CleanupConfig):
        self._client = httpx.Client(timeout=httpx.Timeout(connect=5.0, read=30.0))
    def cleanup(self, text: str) -> str: ...          # Returns original on any failure
    def _openai_cleanup(self, text: str) -> str: ...  # POST {api_base}/chat/completions
    def _anthropic_cleanup(self, text: str) -> str: ... # POST {api_base}/v1/messages
    def close(self) -> None: ...
```

Graceful fallback: any exception -> log warning, return original text. Missing API key -> return original with warning.

**Tests** (`tests/test_cleanup.py`, using `respx`): OpenAI request format, Anthropic request format (different headers/body), disabled -> passthrough, API error -> fallback, missing key -> fallback.

**Exit criteria**:
- [ ] `pytest tests/test_cleanup.py -v` -- all pass
- [ ] Both providers produce correct HTTP requests
- [ ] Graceful fallback on any error

**Commit**: `feat(cleanup): optional AI text cleanup via OpenAI and Anthropic`

---

### Phase 6: Text Injection Module

**Goal**: Copy text to clipboard and simulate Ctrl+V to paste into active app.

**File**: `src/samwhispers/inject.py`

```python
class TextInjector:
    def __init__(self, paste_delay: float = 0.1):
        self._keyboard = Controller()  # Reuse single instance
        self._paste_delay = paste_delay
    def inject(self, text: str) -> None: ...  # clipboard + sleep + Ctrl+V
    def check_clipboard_available(self) -> bool: ...  # Startup check
```

- `time.sleep(paste_delay)` between clipboard write and Ctrl+V (configurable, default 100ms)
- Empty text -> no-op
- Lazy import of `pynput.keyboard.Controller` to avoid X11 crash on headless systems

**Tests** (`tests/test_inject.py`): clipboard round-trip, empty text no-op. Display-dependent tests guarded with `@pytest.mark.skipif(no_display)`.

**Exit criteria**:
- [ ] `pytest tests/test_inject.py -v` -- all pass (display-dependent tests skipped if headless)
- [ ] Clipboard round-trip verified programmatically

**Commit**: `feat(inject): clipboard text injection with paste simulation`

---

### Phase 7: Global Hotkey Listener

**Goal**: Detect push-to-talk hotkey (hold and toggle modes) and trigger callbacks.

**File**: `src/samwhispers/hotkeys.py`

```python
class HotkeyListener:
    def __init__(self, hotkey_str: str, mode: str,
                 on_start: Callable, on_stop: Callable): ...
    def start(self) -> None: ...   # Starts pynput Listener (non-blocking, daemon thread)
    def stop(self) -> None: ...    # Stops listener
    def suppress(self) -> None: ...  # Temporarily ignore events (during paste)
    def resume(self) -> None: ...

def parse_hotkey(hotkey_str: str) -> set[Key | KeyCode]: ...
```

Implementation notes:
- **Hold mode**: `keyboard.Listener` with `on_press`/`on_release`. Track pressed keys in a `set`. When all combo keys are pressed -> `on_start()`. When any combo key is released -> `on_stop()`. Filter key repeat events (same key pressed twice without release).
- **Toggle mode**: Track combo press. First complete combo -> `on_start()`, second -> `on_stop()`.
- `parse_hotkey()` maps strings like `"ctrl+shift+space"` to pynput key objects. Handle platform differences (e.g., `Key.ctrl_l` vs `Key.ctrl`).
- `suppress()`/`resume()` prevent simulated Ctrl+V from re-triggering the hotkey.

**Tests** (`tests/test_hotkeys.py`): hotkey string parsing for common combos, mode validation. Actual listener tests are manual.

**Exit criteria**:
- [ ] `pytest tests/test_hotkeys.py -v` -- all pass
- [ ] Hotkey parsing handles: ctrl+shift+space, alt+r, ctrl+space, etc.

**Commit**: `feat(hotkeys): global hotkey listener with hold and toggle modes`

---

### Phase 8: Main App Orchestration

**Goal**: Wire all modules into a state-machine-driven daemon with proper concurrency and lifecycle.

**File**: `src/samwhispers/app.py`

Architecture:
```python
import enum, queue, signal, threading, logging

class State(enum.Enum):
    IDLE = "idle"
    RECORDING = "recording"
    PROCESSING = "processing"

class SamWhispers:
    def __init__(self, config: AppConfig):
        self._state = State.IDLE
        self._lock = threading.Lock()
        self._work_queue: queue.Queue[bytes] = queue.Queue()
        self._shutdown_event = threading.Event()
        # Initialize all components...

    def run(self) -> None:
        """Startup checks, start worker thread, start hotkey listener, block until shutdown."""
        self._startup_checks()  # mic, clipboard, whisper-server
        self._worker = threading.Thread(target=self._process_loop, daemon=True)
        self._worker.start()
        signal.signal(signal.SIGTERM, lambda *_: self._shutdown_event.set())
        self.hotkey_listener.start()
        log.info("Ready. Listening for hotkey...")
        self._shutdown_event.wait()  # Block main thread
        self.shutdown()

    def _on_record_start(self) -> None:
        with self._lock:
            if self._state != State.IDLE:
                log.warning("Busy (%s), ignoring hotkey", self._state.value)
                return
            self._state = State.RECORDING
        self.recorder.start()
        log.info("Recording...")

    def _on_record_stop(self) -> None:
        with self._lock:
            if self._state != State.RECORDING:
                return
            self._state = State.PROCESSING
        wav_bytes = self.recorder.stop()
        self._work_queue.put(wav_bytes)  # Returns immediately

    def _process_loop(self) -> None:
        """Worker thread: dequeue WAV bytes, transcribe, cleanup, inject."""
        while not self._shutdown_event.is_set():
            try:
                wav_bytes = self._work_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                self._process_recording(wav_bytes)
            except Exception:
                log.exception("Pipeline error")
            finally:
                with self._lock:
                    self._state = State.IDLE

    def _process_recording(self, wav_bytes: bytes) -> None:
        min_size = 44 + int(self.config.audio.sample_rate * 0.5 * 2)
        if len(wav_bytes) < min_size:
            log.warning("Recording too short, skipping")
            return
        log.info("Transcribing...")
        text = self.whisper.transcribe(wav_bytes)
        if not text.strip():
            log.warning("Empty transcription, skipping")
            return
        text = self.cleanup.cleanup(text)
        log.info("Result: %s", text)
        self.hotkey_listener.suppress()
        self.injector.inject(text)
        self.hotkey_listener.resume()
        log.info("Done")

    def _startup_checks(self) -> None:
        """Validate mic, clipboard, whisper-server before entering main loop."""
        ...

    def shutdown(self) -> None:
        """Stop all components, close resources."""
        log.info("Shutting down...")
        self._shutdown_event.set()
        self.hotkey_listener.stop()
        self.recorder.close()
        self.whisper.close()
        self.cleanup.close()
```

**File**: `src/samwhispers/__main__.py` -- argparse with `-c/--config`, `-v/--verbose`, `--version`. Catches `KeyboardInterrupt` -> sets shutdown event.

**Tests** (`tests/test_app.py`): full pipeline with mocked components, state transitions, short recording skip, empty transcription skip, cleanup failure fallback, concurrent hotkey rejection.

**Exit criteria**:
- [ ] `pytest tests/ -v` -- full suite passes
- [ ] `make check` passes (lint + typecheck + tests)
- [ ] `python -m samwhispers --help` shows usage
- [ ] `python -m samwhispers -v` starts, runs startup checks, reports status

**Commit**: `feat(app): state-machine daemon with worker thread and lifecycle management`

---

### Phase 9: README and Documentation

**Goal**: Write setup instructions, usage guide, and troubleshooting.

Sections: What is SamWhispers, Prerequisites, whisper-server setup (build + model download + start), Install, Configuration, Usage, AI cleanup setup, Troubleshooting (no mic, server down, Wayland, pynput permissions, xclip missing), Known limitations.

**Exit criteria**:
- [ ] README covers full setup from zero to working
- [ ] A new user could follow the README and get it running

**Commit**: `docs: README with setup, usage, and troubleshooting`

---

### Phase 10: Integration Testing and Polish

**Goal**: End-to-end verification, edge case hardening, final polish.

Tasks:
1. Generate `tests/fixtures/sample.wav` (synthetic 16kHz WAV)
2. Integration test: mock whisper-server endpoint, full pipeline from WAV to clipboard
3. Verify all error messages are actionable
4. Add `--version` flag
5. Add structured log fields (recording duration, WAV size, transcription latency, cleanup latency)
6. Final `make check` pass

**Exit criteria**:
- [ ] `make check` passes clean
- [ ] All error paths produce actionable log messages
- [ ] No `mypy` errors in strict mode
- [ ] No `ruff` warnings

**Commit**: `test: integration tests and edge case hardening`

---

## 6) Risk Assessment

| Risk | Impact | Mitigation |
|---|---|---|
| pynput doesn't work on Wayland | No hotkey detection on modern Linux desktops | Document X11 requirement. Wayland support deferred to v2 |
| whisper-server API changes | Transcription breaks | Pin to whisper.cpp stable release (v1.8.1), test against known `/inference` API |
| Audio device permissions | Recording fails silently | Check device availability on startup, clear error message |
| Clipboard race condition | Paste happens before clipboard is written | Configurable delay (default 100ms) between clipboard write and Ctrl+V |
| pynput requires root on some Linux setups | Hotkeys don't work | Document: add user to `input` group, or run with appropriate permissions |
| Long recordings fill memory | OOM for very long dictation | `max_duration` timer in AudioRecorder (default 300s), auto-stops with warning |
| Simulated Ctrl+V re-triggers hotkey | Infinite loop or double-paste | Suppress hotkey listener during text injection |
| Thread race between hotkey and audio | Corrupted state or double-start | State machine with lock; reject actions in wrong state |
| whisper-server hangs | Daemon blocks forever | Explicit HTTP timeouts (connect=5s, read=120s) |
| Cleanup API hangs | Pipeline blocks | Separate timeout (connect=5s, read=30s), fallback to raw text |

## 7) Verification

### Automated (run by agent after each phase)
```bash
make check          # lint + typecheck + tests
```

### Manual (user runs once after Phase 8)
1. Start whisper-server: `./whisper-server -m models/ggml-base.en.bin`
2. Start SamWhispers: `python -m samwhispers -v`
3. Open a text editor
4. Hold Ctrl+Shift+Space, speak, release
5. Verify text appears in editor

### Smoke test script (agent can run to verify non-interactive parts)
```bash
# Verify config loading
python -c "from samwhispers.config import load_config; c = load_config(); print(c)"

# Verify WAV encoding
python -c "
from samwhispers.audio import numpy_to_wav
import numpy as np
wav = numpy_to_wav(np.zeros(16000, dtype=np.float32), 16000)
print(f'WAV size: {len(wav)} bytes')
"
```

## 8) Documentation Updates

| Document | Update needed | Phase |
|---|---|---|
| README.md | Full creation | Phase 9 |
| config.example.toml | Created with all options | Phase 1 |

## 9) Autonomous Development Protocol

This section defines how the agent should operate during `/dev` execution.

### Operating rules

1. **Implement each phase fully** before moving to the next. Do not skip ahead.
2. **Run `make check` after every phase**. If tests fail, fix them before committing. Loop up to 5 times.
3. **Make reasonable implementation decisions** without asking. Document non-obvious choices in code comments.
4. **Only stop and ask the user** when:
   - A design decision would change the agreed spec
   - A blocker requires user action (e.g., installing a system package)
   - A test requires manual verification (hotkeys, paste)
5. **Commit after each phase** using the specified commit message.
6. **If a test is flaky or environment-dependent**, mark it with `@pytest.mark.skipif` with a clear reason.
7. **Log everything** -- when in doubt, add a `log.debug()` call.
8. **Run smoke tests** after Phases 2, 3, 4, 5 to verify modules work in isolation.
9. **On import errors from display-dependent libraries** (pynput, pyperclip), use lazy imports and skip affected tests on headless systems.

### Self-verification checklist (run after each phase)

```bash
python -m pytest tests/ -v
mypy src/
ruff check src/ tests/
ruff format --check src/ tests/
python -c "import samwhispers"
```

### Known environment constraints

- Running on WSL (Linux) with access to Windows filesystem
- Python 3.11+ available
- whisper-server may not be running during development -- mock-based tests must pass regardless
- No display server guaranteed during development -- skip tests that require X11/display

## 10) Progress Tracker

| # | Phase | Status | Notes |
|---|---|---|---|
| 1 | Project Scaffolding | Pending | |
| 2 | Configuration Module | Pending | |
| 3 | Audio Capture Module | Pending | |
| 4 | Whisper Server Client | Pending | |
| 5 | AI Cleanup Module | Pending | |
| 6 | Text Injection Module | Pending | |
| 7 | Global Hotkey Listener | Pending | |
| 8 | Main App Orchestration | Pending | |
| 9 | README and Documentation | Pending | |
| 10 | Integration Testing and Polish | Pending | |

## 11) Dependency Graph

```
Phase 1 (scaffolding)
  |
  v
Phase 2 (config) --------+----------+----------+----------+
  |                       |          |          |          |
  v                       v          v          v          v
Phase 3 (audio)    Phase 4 (whisper) Phase 5  Phase 6   Phase 7
  |                       |        (cleanup) (inject)  (hotkeys)
  +-----------------------+----------+----------+----------+
  |
  v
Phase 8 (orchestration)
  |
  v
Phase 9 (docs)
  |
  v
Phase 10 (integration + polish)
```

Phases 3-7 depend only on Phase 2 (config types). They are independent of each other.

## 12) File Change Summary

### Created
- `pyproject.toml`
- `Makefile`
- `config.example.toml`
- `README.md`
- `src/samwhispers/__init__.py`
- `src/samwhispers/__main__.py`
- `src/samwhispers/app.py`
- `src/samwhispers/audio.py`
- `src/samwhispers/config.py`
- `src/samwhispers/cleanup.py`
- `src/samwhispers/hotkeys.py`
- `src/samwhispers/inject.py`
- `src/samwhispers/transcribe.py`
- `tests/__init__.py`
- `tests/conftest.py`
- `tests/test_config.py`
- `tests/test_audio.py`
- `tests/test_transcribe.py`
- `tests/test_cleanup.py`
- `tests/test_inject.py`
- `tests/test_app.py`
- `tests/fixtures/sample.wav`

## 13) Backwards Compatibility

N/A -- greenfield project.

## 14) Review Log

### 2026-03-19 -- Sub-agent Review Cycle 1

Reviewed by: Implementability Reviewer, Reliability Engineer. 32 findings total (8 High, 9 Medium, 15 Low). All High and Medium auto-resolved.

| # | Finding | Severity | Status |
|---|---|---|---|
| I-1 | Wrong setuptools build-backend (`_legacy:_Backend`) | High | Resolved -- changed to `setuptools.build_meta` |
| I-2 | Wrong whisper-server endpoint (claimed OAI-compatible `/v1/audio/transcriptions`) | High | Resolved -- changed to `/inference` endpoint throughout |
| I-4 | `_on_record_stop` runs synchronously on hotkey thread | High | Resolved -- added worker thread + queue architecture in Phase 8 |
| I-9 | Missing `[tool.setuptools.packages.find]` for src layout | High | Resolved -- added `where = ["src"]` |
| R-1 | No thread safety between hotkey callbacks and audio recording | High | Resolved -- state machine with `threading.Lock` in Phase 8 |
| R-2 | Synchronous pipeline on pynput thread (duplicate of I-4) | High | Resolved -- worker thread |
| R-3 | No rapid press protection | High | Resolved -- state machine rejects actions in wrong state |
| R-4 | No HTTP timeout on whisper-server calls | High | Resolved -- explicit `httpx.Timeout` in Phase 4 |
| R-6 | No graceful shutdown / resource cleanup | High | Resolved -- `shutdown()` method, `close()` on all components, signal handling |
| I-3 | No health-check endpoint on whisper-server | Medium | Resolved -- `GET /` returns 200 when ready |
| I-5 | Hold-mode hotkey complexity underestimated | Medium | Resolved -- added implementation notes, key repeat filtering |
| I-7 | Missing delay in inject_text | Medium | Resolved -- configurable delay (default 100ms) |
| I-8 | pynput/pyperclip fail on headless WSL | Medium | Resolved -- lazy imports, skipif guards |
| I-13 | Effort estimate too optimistic (3-5 days) | Medium | Resolved -- revised to 5-7 days |
| R-5 | No cleanup API timeout | Medium | Resolved -- `Timeout(connect=5.0, read=30.0)` |
| R-7 | No SIGTERM handling | Medium | Resolved -- `signal.signal(SIGTERM, ...)` in Phase 8 |
| R-8 | No max duration guard on recording | Medium | Resolved -- `max_duration` + `threading.Timer` in Phase 3 |
| R-9 | InputStream error callback not handled | Medium | Resolved -- error callback in Phase 3 |
| R-10 | No startup audio device validation | Medium | Resolved -- `_startup_checks()` in Phase 8 |
| R-17 | Simulated Ctrl+V may re-trigger hotkey | Medium | Resolved -- `suppress()`/`resume()` on listener during injection |
| I-6 | `load_config` signature mismatch (Path vs str) | Low | Resolved -- accepts `Path | str | None` |
| I-10 | No double-stop guard on AudioRecorder | Low | Resolved -- returns `b""` if not recording |
| I-11 | No graceful shutdown of HotkeyListener | Low | Resolved -- `shutdown()` calls `listener.stop()` |
| I-14 | WAV size check uses magic number | Low | Resolved -- calculated threshold based on sample rate |
| I-15 | sounddevice callback thread safety | Low | Resolved -- `threading.Lock` in AudioRecorder |
| R-11 | httpx clients created per-call | Low | Resolved -- persistent clients in `__init__`, closed in `shutdown()` |
| R-13 | Fixed 50ms paste delay is fragile | Low | Resolved -- configurable, default 100ms |
| R-14 | No retry on transient whisper-server failures | Low | Resolved -- 1 retry with 1s backoff on 5xx |
| R-15 | New pynput Controller on every inject call | Low | Resolved -- single instance reused |
| I-12 | YAGNI: separate OpenAI/Anthropic config dataclasses | Low | Noted -- kept for readability, both are small |
| R-12 | No structured logging / observability | Low | Noted -- added to Phase 10 (log timing per stage) |
| R-16 | No periodic health check for whisper-server | Low | Noted -- deferred, per-request error handling is sufficient for v1 |

## 15) Implementation Divergences from Plan

*Reserved -- filled during implementation*
