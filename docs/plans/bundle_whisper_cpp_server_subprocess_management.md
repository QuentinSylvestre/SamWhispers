# Bundle whisper.cpp Server via Subprocess Management

> **Date**: 2026-07-17
> **Status**: In Progress
> **Scope**: Auto-launch whisper-server as a managed child process, with crash recovery and graceful shutdown
> **Estimated effort**: 1-2 days

---

## 1) Goal

Eliminate the manual step of starting whisper-server separately. SamWhispers spawns and manages the whisper-server process automatically, passing the configured port and model path. The server is health-checked before the app enters its main loop, auto-restarted on crash, and terminated on shutdown.

## 2) Current State

- `config.py:131-133` -- `WhisperConfig` has `server_url: str = "http://localhost:8080"` and `languages: list[str]`. No fields for binary path, model path, or managed mode.
- `transcribe.py:15-18` -- `WhisperClient` is a pure HTTP client. `__init__` takes `server_url` and `language` (default `"auto"`). `health_check()` at line 74 does `GET /` and returns bool.
- `app.py:44-47` -- `WhisperClient` is instantiated in `SamWhispers.__init__` with `config.whisper.server_url` and `language=self._languages[0]`.
- `app.py:202-223` -- `_startup_checks()` calls `self.whisper.health_check()` once. If unreachable, logs a warning and continues.
- `app.py:263-271` -- `shutdown()` calls `self.whisper.close()` (closes the httpx client). No process management.
- `app.py:95-96` -- Signal handling: SIGTERM/SIGINT set `_shutdown_event`, which triggers `shutdown()`.
- `tools/whisper.cpp/` -- whisper.cpp is already cloned and gitignored (`.gitignore:8`). Currently has a Windows build at `build/bin/Release/whisper-server.exe`. Models at `tools/whisper.cpp/models/ggml-*.bin`.
- `wsl.py:13-19` -- `is_wsl()` detects WSL by reading `/proc/version`.

## 3) Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Approach | Subprocess via `subprocess.Popen` | Least invasive; preserves existing HTTP client architecture |
| Default behavior | Managed mode on (`whisper.managed = true`) | Reduces setup friction; opt-out for advanced users |
| Binary path default | `tools/whisper.cpp/build/bin/whisper-server` (relative to CWD) | Matches existing `tools/` convention; `WhisperServerManager` resolves platform-specific path variants (see Phase 2) |
| Model path default | `tools/whisper.cpp/models/ggml-base.en.bin` (relative to CWD) | Matches README recommendation |
| Port passing | Parse port from `server_url`, pass as `--port` to subprocess | Single source of truth for port config |
| Crash recovery | Background thread monitors `process.poll()`, auto-restarts + logs error | Keeps the app functional without user intervention |
| Shutdown | Unix: SIGTERM, wait up to 5s, then SIGKILL. Windows: `TerminateProcess` (immediate kill; no graceful signal available) | Graceful where supported, hard kill otherwise |
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

`src/samwhispers/config.py` -- modify `WhisperConfig` dataclass (line 131):

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
    import os

    bin_path = Path(config.whisper.server_bin)
    if not bin_path.is_file():
        raise ValueError(
            f"whisper.server_bin not found: {bin_path.resolve()}. "
            "Build whisper.cpp first (see README) or set whisper.managed = false."
        )
    if not os.access(bin_path, os.X_OK):
        raise ValueError(
            f"whisper.server_bin is not executable: {bin_path.resolve()}. "
            "Run: chmod +x " + str(bin_path.resolve())
        )
    model_path = Path(config.whisper.model_path)
    if not model_path.is_file():
        raise ValueError(
            f"whisper.model_path not found: {model_path.resolve()}. "
            "Download a model first (see README) or set whisper.managed = false."
        )
```

> **Note**: `os.access(..., os.X_OK)` always returns `True` on Windows, so the executable check is harmless cross-platform and only adds value on Linux/macOS.

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
- [x] `WhisperConfig` has `managed`, `server_bin`, `model_path` fields with defaults
- [x] Validation rejects missing binary/model when `managed = true`
- [x] Validation is skipped when `managed = false`
- [x] `config.example.toml` documents the new fields
- [x] Existing config files without the new fields still load (defaults apply)

### Phase 2: Server manager module

**Goal**: Create `WhisperServerManager` to handle subprocess lifecycle.

Create `src/samwhispers/server.py`:

```python
"""Managed whisper-server subprocess."""

from __future__ import annotations

import atexit
import logging
import subprocess
import sys
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
_MAX_RESTARTS = 5
_RESTART_BACKOFF = 2.0


def _resolve_server_bin(raw_path: str) -> str:
    """Resolve the whisper-server binary path, handling platform variants.

    On Windows, if the given path doesn't exist, try appending Release/ to the
    parent directory and .exe to the filename -- matching the default CMake
    output layout on Windows (build/bin/Release/whisper-server.exe).
    """
    p = Path(raw_path)
    if p.is_file():
        return str(p.resolve())
    if sys.platform == "win32":
        # Try: <parent>/Release/<name>.exe
        win_candidate = p.parent / "Release" / (p.name + ".exe")
        if win_candidate.is_file():
            return str(win_candidate.resolve())
    return str(p.resolve())  # return as-is; validation will catch missing files


class WhisperServerManager:
    """Spawn, monitor, and restart whisper-server."""

    def __init__(self, config: WhisperConfig) -> None:
        self._config = config
        self._proc: subprocess.Popen[bytes] | None = None
        self._lock = threading.Lock()  # guards self._proc access
        self._monitor_thread: threading.Thread | None = None
        self._stop_event = threading.Event()

        parsed = urlparse(config.server_url)
        self._host = parsed.hostname or "127.0.0.1"
        self._port = str(parsed.port or 8080)

        if self._host not in ("127.0.0.1", "localhost", "::1"):
            log.warning(
                "whisper.server_url binds managed server to non-loopback host %r. "
                "The whisper-server API has no authentication -- "
                "ensure this is intentional.",
                self._host,
            )

        self._bin = _resolve_server_bin(config.server_bin)
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
        with self._lock:
            self._proc = subprocess.Popen(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )

    def _wait_ready(self) -> None:
        client = WhisperClient(server_url=self._config.server_url)
        deadline = time.monotonic() + _READY_TIMEOUT
        try:
            while time.monotonic() < deadline:
                with self._lock:
                    proc = self._proc
                if proc and proc.poll() is not None:
                    raise RuntimeError(
                        f"whisper-server exited immediately with code {proc.returncode}. "
                        f"Possible causes: binary/model path incorrect, port {self._port} "
                        "already in use, or unsupported hardware. "
                        "Run the binary manually to see full output."
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
        restart_count = 0
        while not self._stop_event.is_set():
            with self._lock:
                proc = self._proc
            if proc and proc.poll() is not None:
                # Re-check stop event after detecting crash to avoid restart during shutdown
                if self._stop_event.is_set():
                    return
                restart_count += 1
                if restart_count > _MAX_RESTARTS:
                    log.error(
                        "whisper-server has crashed %d times; giving up. "
                        "Transcription will be unavailable.",
                        restart_count - 1,
                    )
                    return
                log.error(
                    "whisper-server crashed (exit code %d), restarting "
                    "(attempt %d/%d)...",
                    proc.returncode,
                    restart_count,
                    _MAX_RESTARTS,
                )
                time.sleep(min(_RESTART_BACKOFF * restart_count, 10.0))
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
        with self._lock:
            proc, self._proc = self._proc, None
        if proc and proc.poll() is None:
            log.info("Stopping whisper-server (pid %d)...", proc.pid)
            proc.terminate()
            try:
                proc.wait(timeout=_SHUTDOWN_GRACE)
            except subprocess.TimeoutExpired:
                log.warning("whisper-server did not exit gracefully, killing")
                proc.kill()
                proc.wait()
```

**Exit criteria**:
- [x] `WhisperServerManager.start()` spawns the process and blocks until health check passes
- [x] `_monitor_loop` detects crash and auto-restarts (max 5 attempts with backoff)
- [x] `stop()` terminates gracefully on Unix (SIGTERM + 5s grace + SIGKILL fallback), immediately on Windows (`TerminateProcess`)
- [x] `stop()` is concurrent-safe via swap-to-local pattern (lock protects `self._proc`)
- [x] `atexit` handler registered as safety net
- [x] `TimeoutError` raised with helpful message if server doesn't become ready
- [x] `_resolve_server_bin()` finds Windows `Release/*.exe` variant when plain path missing
- [x] Non-loopback host emits a warning about unauthenticated API exposure

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

`tests/test_config.py` -- update existing tests and add new ones:
- **`test_defaults`** (existing): must set `managed = false` in a TOML file or patch `Path.is_file` to avoid the new `_validate()` check failing on non-existent binary/model paths. The simplest fix is to write a minimal `[whisper]\nmanaged = false\n` TOML in `tmp_path` and load it explicitly.
- Add: test that default `WhisperConfig` has `managed=True`, correct `server_bin` and `model_path` defaults (direct dataclass instantiation, no `load_config()`)
- Add: test that `load_config()` raises `ValueError` when `managed=True` and binary file doesn't exist
- Add: test that `load_config()` raises `ValueError` when `managed=True` and model file doesn't exist
- Add: test that `load_config()` passes when `managed=False` regardless of file existence
- Add: test that executable check (`os.access`) raises `ValueError` for non-executable binary on non-Windows

`tests/test_app.py` -- update `_make_app` helper:
- Set `config.whisper.managed = False` before constructing `SamWhispers(config)` to prevent `WhisperServerManager` creation and avoid path validation issues. Alternatively, patch `WhisperServerManager` in the `with patch(...)` block. The `managed=False` approach is simpler and preferred.
- Add a dedicated test for `shutdown()` with `managed=True` that verifies `_server_manager.stop()` is called before `whisper.close()`.

`tests/test_server.py` -- add unit tests for the new module:
- Test `_resolve_server_bin()` with existing path (returns resolved)
- Test `_resolve_server_bin()` with non-existent path on Windows (finds `Release/*.exe` variant)
- Test `stop()` is idempotent (calling twice does not raise)
- Test `stop()` concurrent safety (two threads calling `stop()` simultaneously)
- Test `_monitor_loop` exits after `_MAX_RESTARTS` failures

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
| Port conflict (another process on configured port) | whisper-server fails to bind | `_wait_ready()` detects early exit and includes port conflict as a possible cause in error message. Since stderr goes to DEVNULL, the message also suggests running the binary manually for full output |
| Zombie process on unclean exit (`kill -9` on SamWhispers) | Orphaned whisper-server | `atexit` handler as safety net; document in troubleshooting |
| Relative path breaks when CWD differs from project root | Binary/model not found | Validation in `_validate()` gives clear error with resolved absolute path |
| Windows build path differs from Linux (`build/bin/Release/*.exe`) | Default `server_bin` not found on Windows | `_resolve_server_bin()` auto-detects the Windows `Release/` + `.exe` variant |
| Windows build in `tools/` confuses WSL users | Wrong binary used | README explicitly states WSL needs Linux build; validation catches non-executable files |
| Monitor thread restarts server during intentional shutdown | Race condition | `threading.Lock` guards all `self._proc` access; `_monitor_loop` re-checks `_stop_event` after detecting crash before attempting restart |
| Perpetually crashing server loops the monitor forever | CPU waste, log noise | Max 5 restart attempts with linear backoff; monitor exits and logs "giving up" message |
| Non-loopback bind (`0.0.0.0`) exposes unauthenticated API | Network exposure | Warning logged at startup when host is not loopback; no blocking (user may intend this) |
| Windows `terminate()` is immediate kill, not graceful | No flush/cleanup opportunity | Acceptable for whisper-server (stateless); documented in design decisions |

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

### 2026-03-25 -- Per-persona sub-agent review -- personas: Implementability reviewer, Reliability engineer, Security auditor

10 findings (3 High, 4 Medium, 3 Low). All auto-resolved.

| # | Finding | Severity | Status |
|---|---|---|---|
| 1 | Race condition: `_monitor_loop` and `stop()` share `self._proc` without a lock; monitor can spawn a new process during shutdown that gets leaked | High | Resolved -- added `threading.Lock` to guard all `self._proc` access; `stop()` uses swap-to-local pattern; `_monitor_loop` re-checks `_stop_event` after detecting crash |
| 2 | Windows binary path: design decision claims "auto-appends `Release/` and `.exe`" but no code implements it; default path always fails on Windows | High | Resolved -- added `_resolve_server_bin()` helper that tries `Release/*.exe` variant on `sys.platform == "win32"`; updated design decisions table prose |
| 3 | `test_defaults` and other `load_config()` tests break when `managed=True` triggers `is_file()` on non-existent binary/model paths in CI | High | Resolved -- added explicit test update guidance: `test_defaults` must set `managed=false` or patch `Path.is_file`; `_make_app()` must set `managed=False`; added `tests/test_server.py` spec |
| 4 | Concurrent double-`stop()` (atexit + shutdown) causes `AttributeError` on NoneType `self._proc` | Medium | Resolved -- `stop()` now uses swap-to-local pattern under lock: `proc, self._proc = self._proc, None`; second caller gets `None` and no-ops |
| 5 | No restart limit: perpetually crashing server loops the monitor thread forever, wasting CPU and filling logs | Medium | Resolved -- added `_MAX_RESTARTS = 5` with linear backoff; monitor exits with "giving up" log message after limit |
| 6 | `stop()` exit criteria says "sends SIGTERM" but on Windows `terminate()` calls `TerminateProcess` (immediate kill) | Medium | Resolved -- updated design decisions table and exit criteria to describe platform-specific behavior |
| 7 | All line number references in "Current State" section were stale (off by 5-56 lines) despite previous review claiming to have fixed them | Medium | Resolved -- re-verified all references against current source |
| 8 | Error message on early exit only suggests binary/model path issues; misses port conflicts and other causes | Low | Resolved -- expanded `RuntimeError` message to enumerate port conflict, hardware, and suggest running binary manually |
| 9 | No executable bit check on `server_bin`; non-executable file passes `is_file()` but fails at `Popen` with confusing `PermissionError` | Low | Resolved -- added `os.access(bin_path, os.X_OK)` check in `_validate()` (harmless no-op on Windows) |
| 10 | Non-loopback host (`0.0.0.0`) silently accepted; managed server bound to all interfaces without warning | Low | Resolved -- added warning log in `WhisperServerManager.__init__` when host is not loopback |
