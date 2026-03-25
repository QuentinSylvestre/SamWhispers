# Bundle whisper.cpp Server via Subprocess Management

> **Date**: 2026-07-17
> **Status**: Draft
> **Scope**: Auto-launch whisper-server as a managed child process, with crash recovery and graceful shutdown
> **Estimated effort**: 1-2 days

---

## 1) Goal

Eliminate the manual step of starting whisper-server separately. SamWhispers spawns and manages the whisper-server process automatically, passing the configured port and model path. The server is health-checked before the app enters its main loop, auto-restarted on crash, and terminated on shutdown.

## 2) Current State

- `config.py:101-102` -- `WhisperConfig` has `server_url: str = "http://localhost:8080"` and `languages: list[str]`. No fields for binary path, model path, or managed mode.
- `transcribe.py:16-20` -- `WhisperClient` is a pure HTTP client. `__init__` takes `server_url` and `language` (default `"auto"`). `health_check()` at line 68 does `GET /` and returns bool.
- `app.py:42-47` -- `WhisperClient` is instantiated in `SamWhispers.__init__` with `config.whisper.server_url` and `language=self._languages[0]`.
- `app.py:175-181` -- `_startup_checks()` calls `self.whisper.health_check()` once. If unreachable, logs a warning and continues.
- `app.py:207-213` -- `shutdown()` calls `self.whisper.close()` (closes the httpx client). No process management.
- `app.py:93-95` -- Signal handling: SIGTERM/SIGINT set `_shutdown_event`, which triggers `shutdown()`.
- `tools/whisper.cpp/` -- whisper.cpp is already cloned and gitignored (`.gitignore:7`). Currently has a Windows build at `build/bin/Release/whisper-server.exe`. Models at `tools/whisper.cpp/models/ggml-*.bin`.
- `wsl.py:8-14` -- `is_wsl()` detects WSL by reading `/proc/version`.

## 3) Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Approach | Subprocess via `subprocess.Popen` | Least invasive; preserves existing HTTP client architecture |
| Default behavior | Managed mode on (`whisper.managed = true`) | Reduces setup friction; opt-out for advanced users |
| Binary path default | `tools/whisper.cpp/build/bin/whisper-server` (relative to CWD) | Matches existing `tools/` convention; Windows auto-appends `Release/` and `.exe` |
| Model path default | `tools/whisper.cpp/models/ggml-base.en.bin` (relative to CWD) | Matches README recommendation |
| Port passing | Parse port from `server_url`, pass as `--port` to subprocess | Single source of truth for port config |
| Crash recovery | Background thread monitors `process.poll()`, auto-restarts + logs error | Keeps the app functional without user intervention |
| Shutdown | SIGTERM, wait up to 5s, then SIGKILL | Graceful with hard fallback |
| Startup readiness | Poll `health_check()` every 500ms, timeout 30s | whisper-server needs time to load the model |
| Extra server args | Not exposed | Users needing custom flags set `managed = false` and run their own server |

## 4) External Dependencies & Costs

### Required external changes

| Category | Change needed | Owner | Status |
|---|---|---|---|
| Build tools | User must have cmake + C++ compiler to build whisper.cpp | User | Documented in README |
| WSL | User must build whisper.cpp inside WSL (Linux binary), not use Windows `.exe` | User | Documented in README |

### Cost impact

None. No new cloud resources, API calls, or dependencies.

## 5) Implementation Phases

### Phase 1: Config changes

**Goal**: Add new fields to `WhisperConfig` for managed server mode.

`src/samwhispers/config.py` -- modify `WhisperConfig` dataclass (line 101):

```python
@dataclass
class WhisperConfig:
    server_url: str = "http://localhost:8080"
    languages: list[str] = field(default_factory=lambda: ["auto"])
    managed: bool = True
    server_bin: str = "tools/whisper.cpp/build/bin/whisper-server"
    model_path: str = "tools/whisper.cpp/models/ggml-base.en.bin"
```

`src/samwhispers/config.py` -- add validation in `_validate()` (after the existing language validation block). When `managed = true`, verify `server_bin` and `model_path` resolve to existing files:

```python
if config.whisper.managed:
    bin_path = Path(config.whisper.server_bin)
    if not bin_path.is_file():
        raise ValueError(
            f"whisper.server_bin not found: {bin_path.resolve()}. "
            "Build whisper.cpp first (see README) or set whisper.managed = false."
        )
    model_path = Path(config.whisper.model_path)
    if not model_path.is_file():
        raise ValueError(
            f"whisper.model_path not found: {model_path.resolve()}. "
            "Download a model first (see README) or set whisper.managed = false."
        )
```

`config.example.toml` -- add new fields to the `[whisper]` section:

```toml
[whisper]
server_url = "http://localhost:8080"
languages = ["auto"]
managed = true                                              # false to use an external whisper-server
server_bin = "tools/whisper.cpp/build/bin/whisper-server"    # path to whisper-server binary
model_path = "tools/whisper.cpp/models/ggml-base.en.bin"    # path to whisper model file
```

**Exit criteria**:
- [ ] `WhisperConfig` has `managed`, `server_bin`, `model_path` fields with defaults
- [ ] Validation rejects missing binary/model when `managed = true`
- [ ] Validation is skipped when `managed = false`
- [ ] `config.example.toml` documents the new fields
- [ ] Existing config files without the new fields still load (defaults apply)

### Phase 2: Server manager module

**Goal**: Create `WhisperServerManager` to handle subprocess lifecycle.

Create `src/samwhispers/server.py`:

```python
"""Managed whisper-server subprocess."""

from __future__ import annotations

import atexit
import logging
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

from samwhispers.config import WhisperConfig
from samwhispers.transcribe import WhisperClient

log = logging.getLogger("samwhispers")

_READY_POLL_INTERVAL = 0.5
_READY_TIMEOUT = 30.0
_SHUTDOWN_GRACE = 5.0


class WhisperServerManager:
    """Spawn, monitor, and restart whisper-server."""

    def __init__(self, config: WhisperConfig) -> None:
        self._config = config
        self._proc: subprocess.Popen[bytes] | None = None
        self._monitor_thread: threading.Thread | None = None
        self._stop_event = threading.Event()

        parsed = urlparse(config.server_url)
        self._host = parsed.hostname or "127.0.0.1"
        self._port = str(parsed.port or 8080)

        self._bin = str(Path(config.server_bin).resolve())
        self._model = str(Path(config.model_path).resolve())

        atexit.register(self.stop)

    def _build_cmd(self) -> list[str]:
        return [self._bin, "-m", self._model, "--host", self._host, "--port", self._port]

    def start(self) -> None:
        """Spawn whisper-server and block until it passes health check."""
        self._stop_event.clear()
        self._spawn()
        self._wait_ready()
        self._monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._monitor_thread.start()

    def _spawn(self) -> None:
        cmd = self._build_cmd()
        log.info("Starting whisper-server: %s", " ".join(cmd))
        # Use DEVNULL for both stdout and stderr to avoid pipe buffer blocking.
        # whisper-server logs are not needed -- errors are detected via health check and poll().
        self._proc = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )

    def _wait_ready(self) -> None:
        client = WhisperClient(server_url=self._config.server_url)
        deadline = time.monotonic() + _READY_TIMEOUT
        try:
            while time.monotonic() < deadline:
                if self._proc and self._proc.poll() is not None:
                    raise RuntimeError(
                        f"whisper-server exited immediately with code {self._proc.returncode}. "
                        "Check that the binary and model path are correct."
                    )
                if client.health_check():
                    log.info("whisper-server is ready on port %s", self._port)
                    return
                time.sleep(_READY_POLL_INTERVAL)
        finally:
            client.close()
        raise TimeoutError(
            f"whisper-server not ready after {_READY_TIMEOUT}s. "
            "The model may be too large -- try a smaller one."
        )

    def _monitor_loop(self) -> None:
        while not self._stop_event.is_set():
            if self._proc and self._proc.poll() is not None:
                log.error(
                    "whisper-server crashed (exit code %d), restarting...",
                    self._proc.returncode,
                )
                try:
                    self._spawn()
                    self._wait_ready()
                except Exception:
                    log.exception("Failed to restart whisper-server")
                    return
            self._stop_event.wait(timeout=1.0)

    def stop(self) -> None:
        """Terminate the managed server process."""
        self._stop_event.set()
        if self._proc and self._proc.poll() is None:
            log.info("Stopping whisper-server (pid %d)...", self._proc.pid)
            self._proc.terminate()
            try:
                self._proc.wait(timeout=_SHUTDOWN_GRACE)
            except subprocess.TimeoutExpired:
                log.warning("whisper-server did not exit gracefully, killing")
                self._proc.kill()
                self._proc.wait()
        self._proc = None
```

**Exit criteria**:
- [ ] `WhisperServerManager.start()` spawns the process and blocks until health check passes
- [ ] `_monitor_loop` detects crash and auto-restarts
- [ ] `stop()` sends SIGTERM, waits 5s, falls back to SIGKILL
- [ ] `atexit` handler registered as safety net
- [ ] `TimeoutError` raised with helpful message if server doesn't become ready

### Phase 3: Wire into SamWhispers app

**Goal**: Integrate `WhisperServerManager` into the app lifecycle.

`src/samwhispers/app.py` -- in `__init__` (after the `WhisperClient` instantiation), conditionally create the server manager:

```python
from samwhispers.server import WhisperServerManager

# After self.whisper = WhisperClient(...)
self._server_manager: WhisperServerManager | None = None
if config.whisper.managed:
    self._server_manager = WhisperServerManager(config.whisper)
```

`src/samwhispers/app.py` -- in `_startup_checks()` (replace the whisper-server health check block):

```python
# Start or check whisper-server
if self._server_manager:
    try:
        self._server_manager.start()
        log.info("Whisper server (managed): OK")
    except (RuntimeError, TimeoutError) as e:
        log.error("Failed to start managed whisper-server: %s", e)
        raise SystemExit(1) from e
elif self.whisper.health_check():
    log.info("Whisper server: OK")
else:
    log.warning(
        "Whisper server at %s is not reachable. "
        "Transcription will fail until it's started.",
        self.config.whisper.server_url,
    )
```

`src/samwhispers/app.py` -- in `shutdown()` (before `self.whisper.close()`):

```python
if self._server_manager:
    self._server_manager.stop()
```

`tests/test_config.py` -- add tests for the new config fields:
- Test that default `WhisperConfig` has `managed=True`, correct `server_bin` and `model_path` defaults
- Test that validation raises `ValueError` when `managed=True` and binary/model files don't exist
- Test that validation passes when `managed=False` regardless of file existence

`tests/test_app.py` -- update the `_make_app` helper to account for the new `WhisperConfig` fields. Mock or set `managed=False` in test fixtures to avoid subprocess spawning during unit tests.

**Exit criteria**:
- [ ] Managed mode: server starts before hotkey listener, app exits on startup failure
- [ ] Unmanaged mode: existing warning-only behavior preserved
- [ ] Shutdown stops the managed server before closing the HTTP client
- [ ] App works identically to before when `managed = false`

### Phase 4: Documentation updates

**Goal**: Update README and example config to reflect the new setup flow.

`README.md` -- replace the "Setting Up whisper-server" section. The new flow:

1. Clone whisper.cpp into `tools/` (or it's already there)
2. Build it (platform-specific cmake commands)
3. Download a model into `tools/whisper.cpp/models/`
4. SamWhispers handles the rest automatically

Add a subsection explaining `whisper.managed = false` for users who want to run their own server.

Add a note in the WSL section that whisper.cpp must be built inside WSL (Linux binary), not the Windows build.

`config.example.toml` -- already updated in Phase 1.

**Exit criteria**:
- [ ] README documents the build-in-`tools/` workflow
- [ ] README documents `managed = false` opt-out
- [ ] WSL section clarifies Linux-native build requirement
- [ ] Old "start whisper-server manually" instructions replaced with new flow

## 6) Risk Assessment

| Risk | Impact | Mitigation |
|---|---|---|
| Large model exceeds 30s startup timeout | App fails to start | Clear error message suggesting smaller model; timeout can be made configurable later |
| Port conflict (another process on configured port) | whisper-server fails to bind | Detect from process exit/stderr, log clear message |
| Zombie process on unclean exit (`kill -9` on SamWhispers) | Orphaned whisper-server | `atexit` handler as safety net; document in troubleshooting |
| Relative path breaks when CWD differs from project root | Binary/model not found | Validation in `_validate()` gives clear error with resolved absolute path |
| Windows build in `tools/` confuses WSL users | Wrong binary used | README explicitly states WSL needs Linux build; validation catches non-executable files |
| Monitor thread restarts server during intentional shutdown | Race condition | `_stop_event` is set before `stop()` terminates the process; monitor checks it first |

## 7) Verification

```bash
# Unit tests
python -m pytest tests/ -v

# Manual: managed mode (default)
# 1. Ensure tools/whisper.cpp/build/bin/whisper-server exists and a model is downloaded
# 2. Run: python -m samwhispers -v
# 3. Verify logs show "Starting whisper-server" and "whisper-server is ready"
# 4. Ctrl+C -- verify logs show "Stopping whisper-server" and clean exit
# 5. Verify no orphaned whisper-server process: pgrep whisper-server

# Manual: crash recovery
# 1. Start samwhispers in managed mode
# 2. Kill whisper-server: kill $(pgrep whisper-server)
# 3. Verify logs show "whisper-server crashed" and "restarting"
# 4. Verify transcription works after restart

# Manual: unmanaged mode
# 1. Set whisper.managed = false in config.toml
# 2. Start whisper-server manually
# 3. Run samwhispers -- verify it connects to existing server
# 4. Stop samwhispers -- verify whisper-server is still running
```

## 8) Documentation Updates

| Document | Update needed | Phase |
|---|---|---|
| `README.md` | Replace whisper-server setup section, add managed/unmanaged docs, WSL clarification | Phase 4 |
| `config.example.toml` | Add `managed`, `server_bin`, `model_path` fields | Phase 1 |

## 9) Implementation Divergences from Plan

_Reserved -- filled during implementation._

## Review Log

### 2026-07-17 -- Self-reviewed (sub-agent unavailable) -- personas: Implementability reviewer, Reliability engineer, Security auditor

7 findings (0 High, 4 Medium, 3 Low). 6 auto-resolved.

| # | Finding | Severity | Status |
|---|---|---|---|
| 1 | Line number references in Current State and Phase 3 were stale (multilingual feature merged since exploration) | Medium | Resolved -- updated all file:line references to match actual source |
| 2 | `server.py` imported `sys` but never used it | Low | Resolved -- removed unused import |
| 3 | `stderr=subprocess.PIPE` in `_spawn()` risks pipe buffer blocking if whisper-server writes heavily to stderr and nobody reads it | Medium | Resolved -- changed to `stderr=subprocess.DEVNULL`; errors detected via health check and poll() instead |
| 4 | `_wait_ready` created `WhisperClient(language="en")` but actual constructor default is `"auto"` | Low | Resolved -- removed explicit language arg |
| 5 | No test changes mentioned in any phase | Medium | Resolved -- added test update guidance to Phase 3 |
| 6 | `atexit` handler and `shutdown()` both call `stop()` -- double invocation | Low | Noted -- `stop()` is already idempotent (checks `poll()` before terminating, sets `_stop_event` which is harmless to set twice). No fix needed. |
| 7 | Subprocess spawning uses list args (no `shell=True`) -- safe from shell injection. `_build_cmd` constructs args from config strings passed directly as list elements. Network binding defaults to `127.0.0.1` parsed from `server_url`. | Medium | Noted -- security posture is adequate. No fix needed. |
