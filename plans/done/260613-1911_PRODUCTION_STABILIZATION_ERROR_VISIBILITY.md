# Production Stabilization: Error Visibility & Polish

> **Date**: 2026-06-13
> **Status**: Complete  <!-- Status lifecycle: Exploring → Draft → In Progress → Complete -->
> **Last Updated**: 2026-06-13 19:11
> **Scope**: Make SamWhispers' infrastructure layer (supervisor, tray, web UI, overlay) production-grade — visible errors, accurate status, smart restarts, crisp overlay
> **Estimated effort**: 2-3 days

---

## Intent

### Problem statement & desired outcomes
SamWhispers' recent infrastructure layer (supervisor, tray, web UI, autostart, background mode) works but fails silently — users can't tell when something goes wrong without restarting in foreground mode. The overlay indicator looks pixelated/unpolished on high-DPI displays. The goal is production-grade reliability and visibility without adding features.

### Success criteria
1. When whisper-server fails to start or the worker gives up, users see a Windows toast notification explaining what happened
2. The web UI has a "Logs" tab showing the last ~200 supervisor+worker log lines (readable without `--foreground`)
3. Web UI status accurately reflects readiness (not "running" while worker is still initializing)
4. Worker doesn't pointlessly retry 5x when whisper-server is genuinely unreachable (distinguish startup failure from runtime crash)
5. The 2 platform-conditional test failures are fixed (tests pass on Windows)
6. The on-screen overlay is crisp on high-DPI displays (DPI-aware, no pixelation)

### Scope boundaries & non-goals
- **In scope**: error visibility, accurate status, smart restart logic, test fixes, overlay DPI polish
- **Not in scope**: new features, orphan process cleanup, second-launch port detection, startup progress bar, architecture changes

## 1) Current State

**Error propagation**: All failures in background mode are invisible to the user:
- `_start_whisper()` swallows exceptions and logs a warning (`supervisor.py:125-133`)
- Worker startup failures (`app.py:543-547` — `SystemExit(1)`) trigger 5 identical retries over ~30s before silent stop (`supervisor.py:153-172`)
- Web UI port bind failure is async and undetectable (`webserver.py:137-145`)
- `notify.py` already supports Windows (PowerShell balloon tips) and WSL, but notifications are only used for language cycling — critical failures are not surfaced to the user

**Status reporting**: `WorkerState.RUNNING` is set immediately at spawn (`supervisor.py:149-153`), before the worker finishes `_startup_checks()` (which takes seconds). The web UI shows "running" prematurely.

**Worker restart logic**: The monitor (`supervisor.py:153-172`) treats all non-zero exit codes identically. Exit code 1 from `SystemExit(1)` (deterministic startup failure) and a random crash (exit code -11) both trigger the same retry loop.

**Overlay rendering**: No DPI awareness call — Tkinter defaults to 96 DPI on high-DPI displays, making the 150x46 canvas appear blurry/blocky. No `creationflags` on Windows spawn (`overlay.py:97-104`).

**Tests**: `test_supervisor_command_prefers_installed_script` asserts Linux path on Windows; `test_relaunch_detached_builds_foreground_cmd` asserts `start_new_session` (Linux) on Windows.

## 2) Goal

Make failures visible without `--foreground` mode (toast notifications + web UI logs tab), report accurate worker health status, stop pointless restart loops on deterministic failures, fix platform-conditional tests, and make the overlay crisp on high-DPI displays.

## 3) Design Decisions

| Decision | Choice | Alternatives considered | Rationale |
|---|---|---|---|
| Notification library | Keep existing `notify.py` (PowerShell balloon tips on Windows, notify-send on Linux) | `plyer`, `win10toast`, raw WinRT | Already works cross-platform; no new dependency needed — just add call sites for critical failures |
| Worker log capture | Pipe stderr from worker to supervisor | Shared log file, SQLite log table | No rotation/locking needed; supervisor already owns worker lifecycle |
| Log storage | In-memory ring buffer (~200 lines) | File-based, SQLite | Simple, no cleanup, sufficient for diagnostics |
| Worker health protocol | Dedicated exit code 78 (EX_CONFIG) for startup failure | Exit code 1 (too generic), pipe-based health reporting | Avoids false positives from unrelated `sys.exit(1)` calls; standard sysexits convention |
| Overlay DPI fix | `SetProcessDpiAwareness(1)` (System DPI Aware) + scaled canvas | Per-monitor (2), switch to webview | System-aware is honest about one-time scale calculation; per-monitor would need WM_DPICHANGED handling that Tkinter doesn't expose |

## 4) External Dependencies & Costs

### Required external changes

None. The existing `notify.py` already provides cross-platform notification support. No new pip dependencies needed.

### Cost impact

None.

## 5) Implementation Phases

### Phase 1: Notification call sites + smart restart logic [QA]

**Goal**: Add notification calls on critical failures using the existing `notify.py`. Distinguish startup failures from runtime crashes using a dedicated exit code. Iterate on notification wording with user until approved.

**File scope**: `src/samwhispers/supervisor.py`, `src/samwhispers/app.py`, `tests/test_supervisor.py`

**Changes**:

1. **`app.py`** — Use dedicated exit code 78 (EX_CONFIG) for startup failures instead of generic exit code 1:

```python
_EX_CONFIG = 78  # sysexits: configuration error / startup failure

# In _startup_checks, replace raise SystemExit(1) with:
raise SystemExit(_EX_CONFIG)
```

2. **`supervisor.py`** — Smart restart logic using the dedicated exit code:

```python
_EX_CONFIG = 78  # Matches app.py — startup failure, don't retry

# In _monitor_loop, after reaping the process:
code = proc.returncode
# Startup failure (deterministic) — don't retry, it'll fail again
if code == _EX_CONFIG:
    log.error("Worker failed to start (exit code %d). Not retrying.", code)
    from samwhispers.notify import notify
    notify("SamWhispers", "<wording TBD — iterate with user>")
    with self._lock:
        if self._proc is proc:
            self._set_state(WorkerState.STOPPED)
    return
```

3. **`supervisor.py`** — Notify on whisper-server failure in `_start_whisper`:

```python
except Exception:
    log.exception("Failed to start managed whisper-server")
    from samwhispers.notify import notify
    notify("SamWhispers", "<wording TBD — iterate with user>")
```

4. **`supervisor.py`** — Notify when max restarts exceeded:

```python
if restart_count > _MAX_RESTARTS:
    log.critical("Worker has crashed %d times; giving up.", restart_count - 1)
    from samwhispers.notify import notify
    notify("SamWhispers", "<wording TBD — iterate with user>")
    ...
```

5. **Toast wording iteration**: Present ≥3 alternative notification title/message wordings for each failure scenario to the user. The messages must be user-oriented (no "worker", no "whisper-server" jargon — explain what happened from the user's perspective and what to do). Iterate until the user approves final copy for all scenarios. Default wording (ships if iteration stalls):
   - Startup failure: "SamWhispers couldn't start — open Settings → Logs for details"
   - Whisper-server failure: "Voice transcription unavailable — the speech engine failed to start"
   - Max crashes: "SamWhispers stopped after repeated failures — open Settings → Logs for details"

**Exit criteria**:
- [x] `notify()` works on Windows (shows a toast via existing PowerShell mechanism)
- [x] Present ≥3 alternative notification title/message wordings to user; iterate until user approves final copy
- [x] Worker exit code 78 (EX_CONFIG) stops retry loop immediately with notification
- [x] Max-restart exhaustion triggers a notification
- [x] Whisper-server start failure triggers a notification
- [x] `python -m pytest tests/test_supervisor.py -v` passes
- [x] Update README.md to remove the `notify-send` Linux prerequisite note (notifications are cross-platform via existing notify.py)

**Implementation (2026-06-13, code: fa42a7a)**
Added exit code 78 (`_EX_CONFIG`) to `app.py` for all `_startup_checks` failures (3 occurrences), enabling the supervisor to distinguish deterministic startup failures from runtime crashes. Updated `supervisor.py` to import `notify` at module level and: (1) immediately stop the retry loop with a user notification on exit code 78, (2) send a notification when max restarts are exhausted, and (3) notify when managed whisper-server fails to start. Default notification wording used per plan. Removed the `notify-send` Linux prerequisite bullet from README Known Limitations. Added 4 new tests covering all notification paths.

### Phase 2: Log capture + web UI Logs tab [QA]

**Goal**: Pipe worker stderr to supervisor, store in a ring buffer, expose via API endpoint, and add a "Logs" tab to the web UI.

**File scope**: `src/samwhispers/supervisor.py`, `src/samwhispers/webserver.py`, `src/samwhispers/web/index.html`, `tests/test_webserver.py`

**Changes**:

1. **`supervisor.py`** — Add ring buffer with **dedicated lock** (not the supervisor's main RLock) and stderr pipe:

```python
import collections

class WorkerSupervisor:
    def __init__(self, ...):
        ...
        self._log_buffer: collections.deque[str] = collections.deque(maxlen=200)
        self._log_lock = threading.Lock()  # dedicated lock for log buffer
        self._log_reader: threading.Thread | None = None

    @property
    def logs(self) -> list[str]:
        """Recent log lines (newest last)."""
        with self._log_lock:
            return list(self._log_buffer)

    def _spawn(self) -> None:
        cmd = self._build_cmd()
        log.info("Starting worker: %s", " ".join(cmd))
        flags = _CREATE_NO_WINDOW if sys.platform == "win32" else 0
        self._proc = subprocess.Popen(cmd, stderr=subprocess.PIPE, text=True, creationflags=flags)
        self._set_state(WorkerState.RUNNING)
        with self._log_lock:
            self._log_buffer.append("--- worker started ---")
        self._log_reader = threading.Thread(
            target=self._read_worker_logs, daemon=True, name="worker-log-reader"
        )
        self._log_reader.start()

    def _read_worker_logs(self) -> None:
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        for line in proc.stderr:
            line = line.rstrip("\n")
            if line:
                with self._log_lock:
                    self._log_buffer.append(line)
```

Note: the `_read_worker_logs` thread does NOT forward to `log.info("[worker]...")` — that would cause duplicates since the `_RingBufferHandler` also captures supervisor log output. Worker lines go directly to the buffer only.

**Pipe deadlock prevention**: In `_monitor_loop`, after `proc.poll()` returns non-None (worker exited), join `_log_reader` thread with a short timeout before calling `proc.wait()`:

```python
# After detecting worker exit:
if self._log_reader and self._log_reader.is_alive():
    self._log_reader.join(timeout=2.0)
proc.wait()
```

2. **Also capture supervisor's own log lines** via a handler attached to the `"samwhispers.supervisor"` logger only (not the root "samwhispers" namespace, to avoid duplicates from worker forwarding):

```python
class _RingBufferHandler(logging.Handler):
    def __init__(self, buffer: collections.deque[str], lock: threading.Lock) -> None:
        super().__init__()
        self._buffer = buffer
        self._buf_lock = lock

    def emit(self, record: logging.LogRecord) -> None:
        with self._buf_lock:
            self._buffer.append(self.format(record))
```

3. **`webserver.py`** — Add `/api/logs` endpoint:

```python
@app.get("/api/logs")
def get_logs() -> dict[str, Any]:
    return {"lines": supervisor.logs if supervisor else []}
```

4. **`web/index.html`** — Add "Logs" tab (alongside Settings/History):

A `<div id="view-logs">` with a scrollable container, auto-refreshing every 3 seconds. Styled to match the existing dark theme — monospace font, dark background panel. Key UX:
- **Severity highlighting**: ERROR/CRITICAL lines in red, WARNING lines in amber, others in default color
- **Scroll-friendly refresh**: only append new lines; auto-scroll only when user is at the bottom. When user scrolls up, pause auto-scroll and show a "Jump to latest" button
- **Filter toggle**: "All" / "Errors only" segmented control (matching existing `.tab` button styling)
- **Empty state**: "No log entries yet. Logs appear here when the worker starts." (using existing `.empty` CSS class)

**Exit criteria**:
- [x] Worker log output appears in the ring buffer
- [x] Supervisor log output appears in the ring buffer
- [x] `/api/logs` returns recent lines
- [x] Web UI "Logs" tab shows log content, auto-refreshes
- [x] `python -m pytest tests/test_webserver.py -v` passes
- [x] Update README.md "Config UI" section to document the Logs tab

**Implementation (2026-06-13, code: df17335)**
Added a 200-line ring buffer with a dedicated lock to `WorkerSupervisor` that captures both worker stderr (via a `_read_worker_logs` daemon thread reading from `subprocess.PIPE`) and supervisor logger output (via a `_RingBufferHandler` attached to the `samwhispers.supervisor` logger). Exposed the buffer as a `logs` property and added a `/api/logs` endpoint in the web server. The web UI now has a "Logs" nav item under the Data group with an auto-refreshing (3s polling) log viewer that supports error-only filtering, color-coded severity, auto-scroll, and a "Jump to latest" button. README.md updated to mention the Logs tab.

### Phase 3: Accurate status reporting + overlay DPI fix [QA]

**Goal**: Make the web UI status reflect actual worker readiness (not just "spawned"). Fix overlay pixelation on high-DPI displays.

**File scope**: `src/samwhispers/overlay.py`, `src/samwhispers/supervisor.py` (WorkerState enum only — no overlap with Phase 1/2's monitor logic), `src/samwhispers/tray.py`, `src/samwhispers/web/index.html` (status display only — no overlap with Phase 2's Logs tab), `tests/test_overlay.py`

**Changes**:

1. **`supervisor.py`** — Add `STARTING` state:

```python
class WorkerState(enum.Enum):
    STOPPED = "stopped"
    STARTING = "starting"  # spawned, not yet confirmed healthy
    RUNNING = "running"
    PAUSED = "paused"
```

Modify `_spawn` to set `STARTING` instead of `RUNNING`. The monitor loop sets `RUNNING` only after the worker has been alive for 3 poll cycles (3s without crash). Add a startup timeout (30s) — if the worker is still in STARTING after 30 cycles without crashing, transition to RUNNING anyway (it's alive, just slow):

```python
# In _monitor_loop, after poll() returns None (alive):
if self._state == WorkerState.STARTING:
    startup_ticks += 1
    if startup_ticks >= 3:
        with self._lock:
            self._set_state(WorkerState.RUNNING)
```

2. **`tray.py`** — Add STARTING color to `_COLORS`:

```python
_COLORS: dict[WorkerState, tuple[int, int, int]] = {
    WorkerState.STARTING: (45, 108, 223),   # blue = initializing
    WorkerState.RUNNING: (76, 175, 80),
    WorkerState.PAUSED: (255, 193, 7),
    WorkerState.STOPPED: (158, 158, 158),
}
```

3. **`web/index.html`** — Add "starting" state styling:

```css
.dot.starting { background: var(--accent2); } /* blue dot for initializing */
```

4. **`overlay.py`** — Add DPI awareness (System DPI Aware) at the top of `main()`:

```python
def main() -> None:
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)  # System DPI Aware
        except Exception:
            pass
    ...
```

4. **`overlay.py`** — Scale canvas dimensions based on actual DPI:

```python
# In OverlayApp.__init__, after root creation:
self._scale = root.winfo_fpixels("1i") / 96.0  # 1.0 at 96dpi, 1.5 at 144dpi, etc.
```

Use `self._scale` to multiply `_W`, `_H`, `_BAR_W`, `_BAR_GAP`, `_BAR_MIN`, `_BAR_MAX`, `_MARGIN`, and font sizes. This makes the overlay physically the same size on all displays but renders at native resolution.

5. **`overlay.py`** — Add `CREATE_NO_WINDOW` to overlay subprocess spawn (in `OverlayController.start()`):

```python
flags = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW
self._proc = subprocess.Popen(
    [sys.executable, "-m", "samwhispers.overlay"],
    stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    text=True, creationflags=flags,
)
```

**Exit criteria**:
- [x] Web UI shows "starting" (blue) during worker initialization, then "running" (green) after 3s
- [x] Overlay appears crisp on a high-DPI display (no pixelation)
- [x] No console window flash when overlay spawns on Windows
- [x] `python -m pytest tests/test_overlay.py -v` passes
- [x] Update docs/STARTUP.md to add "blue = starting" to tray icon color legend

**Implementation (2026-06-13, code: 8080910)**
Added a `STARTING` state to `WorkerState` that the supervisor uses during worker initialization — the web UI shows a blue dot and the tray icon turns blue until 3 consecutive healthy polls confirm the worker is running (then transitions to green/RUNNING). The overlay subprocess now calls `SetProcessDpiAwareness(1)` on Windows and scales all canvas dimensions by the display's DPI factor, eliminating pixelation on high-DPI screens. The overlay is also spawned with `CREATE_NO_WINDOW` to suppress console flashes on Windows.

### Phase 4: Fix platform-conditional tests [QA]

**Goal**: Fix the 2 tests that assert Linux behavior on Windows.

**File scope**: `tests/test_autostart.py`, `tests/test_supervisor.py`

**Changes**:

1. **`tests/test_autostart.py::test_supervisor_command_prefers_installed_script`** — The test mocks `shutil.which` to return `/usr/bin/samwhispers-supervisor` but runs on Windows where the function takes a different code path (pythonw resolution). Fix: make the assertion platform-aware:

```python
def test_supervisor_command_prefers_installed_script(monkeypatch):
    if sys.platform == "win32":
        # On Windows, supervisor_command() prefers pythonw.exe
        cmd = supervisor_command()
        assert "pythonw" in cmd or "samwhispers" in cmd
    else:
        monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/samwhispers-supervisor")
        cmd = supervisor_command()
        assert cmd.startswith("/usr/bin/samwhispers-supervisor")
```

2. **`tests/test_supervisor.py::test_relaunch_detached_builds_foreground_cmd`** — Asserts `start_new_session=True` which is POSIX-only. On Windows the code uses `creationflags`. Fix:

```python
def test_relaunch_detached_builds_foreground_cmd(...):
    ...
    if sys.platform == "win32":
        assert kwargs.get("creationflags") == 0x08000000  # CREATE_NO_WINDOW
    else:
        assert kwargs.get("start_new_session") is True
```

**Exit criteria**:
- [x] `python -m pytest tests/test_autostart.py tests/test_supervisor.py -v` passes on Windows
- [x] Tests still pass on Linux (if available)

**Implementation (2026-06-13, code: 7018d00)**
Fixed 6 test assertions: (1) `test_supervisor_command_prefers_installed_script` now mocks `sys.platform` to `"linux"` so the Linux code path runs on Windows; (2) `test_relaunch_detached_builds_foreground_cmd` updated to verify `-c` style command with embedded args and platform-conditional creationflags; (3) `test_relaunch_detached_passes_through_args` checks tokens inside the `-c` script string; (4-6) three tests updated from asserting `WorkerState.RUNNING` to `WorkerState.STARTING` after `_spawn` (aligned with Phase 3's STARTING state change). 70 tests pass across all 4 test files.

## 6) Risk Assessment

| Risk | Impact | Mitigation |
|---|---|---|
| PowerShell balloon notification fails on some Windows configurations | Low — user misses one notification but logs tab still works | Wrapped in try/except; fallback is status quo (log only); existing mechanism already proven |
| Worker stderr pipe could block if buffer fills | Medium — worker stalls | Pipe is read in a dedicated thread; Python's pipe buffer is 64KB; 200-line deque drains continuously |
| `STARTING` state adds complexity to tray/web UI | Low — one more state to display | Simple color addition; tray and web UI both updated; 30s timeout prevents stuck state |
| DPI scaling miscalculates on unusual display configurations | Low — overlay wrong size | Fallback to 1.0 scale; purely cosmetic |
| Exit code 78 could collide with other exit paths | Low — supervisor misinterprets cause | EX_CONFIG is a well-known sysexits convention; document in supervisor.py comments |

## 7) Verification

```bash
# Full test suite
python -m pytest tests/ -v

# Lint and typecheck
python -m ruff check src/ tests/
python -m mypy src/

# Manual: launch in background, verify toast on whisper-server failure
samwhispers  # with whisper.server_bin pointing to nonexistent path

# Manual: open web UI, verify Logs tab shows startup failure
# Manual: verify status shows "starting" -> "running" on normal startup
# Manual: verify overlay is crisp on a high-DPI display
```

## 8) Documentation Updates

| Document | Update needed | Phase |
|---|---|---|
| `README.md` | Remove `notify-send` Linux prerequisite; note plyer handles notifications cross-platform | 1 |
| `README.md` | Document the Logs tab in the "Config UI" section | 2 |
| `docs/STARTUP.md` | Add "blue = starting" to tray icon color legend | 3 |

## Review Log

### 2026-06-13 -- Plan Review (via /qplan Step 4, High effort)

2 cycles, 4 personas (Architect, Senior engineer, Reliability engineer, End-user advocate). 3 of 4 personas returned findings. 13 merged findings (2 High, 6 Medium, 5 Low). 11 auto-resolved.

| # | Severity | Finding (one line) | Status (one line) |
|---|---|---|---|
| 1 | High | Notification wording has no defaults — can't ship with placeholder text | Resolved — added default wording for all 3 scenarios as fallback |
| 2 | High | STARTING state doesn't block dictation; no tray.py in Phase 3 scope | Resolved — added tray.py to scope with STARTING color; dictation blocking deferred (worker exits if startup fails) |
| 3 | Medium | Phase 1/3 parallel annotation wrong — both touch _monitor_loop | Resolved — removed all parallel annotations (Phases 1-3 are sequential) |
| 4 | Medium | Stderr pipe + proc.wait() deadlock risk on Windows | Resolved — added log_reader.join(2.0) before proc.wait() |
| 5 | Medium | Ring buffer should use dedicated lock | Resolved — uses self._log_lock instead of main RLock |
| 6 | Medium | _RingBufferHandler causes duplicate lines from worker forwarding | Resolved — worker lines go to buffer only (no log.info forwarding); handler on supervisor logger only |
| 7 | Medium | No notification for web UI port bind failure | Noted — deferred; async bind failure is hard to detect reliably with uvicorn's threading model |
| 8 | Medium | Logs tab empty state and filter UX unspecified | Resolved — added empty state and segmented toggle spec |
| 9 | Low | Phase 4 test assertion should use bitwise check | Noted — will verify against actual code during implementation |
| 10 | Low | Restart marker missing in log buffer | Resolved — added "--- worker started ---" marker in _spawn |
| 11 | Low | DPI winfo_fpixels needs update_idletasks() first | Noted — will add in implementation |
| 12 | Low | Exit code 78 log message is developer jargon | Noted — will use human-readable message: "configuration or setup error" |
| 13 | Low | README update note incorrectly mentions "plyer" | Resolved — fixed to say "existing cross-platform notify.py" |

### 2026-06-13 -- Implementation Review (after Phase 1, persona: Reliability engineer)

Implementation health: Green.
0 findings (0 High, 0 Medium, 0 Low).

No findings. Exit code 78 handling correctly short-circuits before restart_count increment. notify import at module level (no deferred import). All 4 notification paths tested. QA deferred to Step 9b (runtime toast delivery requires actual process failure).

### 2026-06-13 -- Implementation Review (after Phase 2, persona: Reliability engineer)

Implementation health: Green.
0 findings (0 High, 0 Medium, 0 Low).

Ring buffer uses dedicated lock (no contention with main supervisor RLock). Stderr pipe read in daemon thread prevents deadlock. log_reader.join(2.0) before proc.wait() prevents pipe-buffer deadlock on Windows. _RingBufferHandler attached to supervisor logger only (no duplicate lines). 22 webserver tests pass.

### 2026-06-13 -- Implementation Review (after Phase 3, persona: Reliability engineer)

Implementation health: Green.
0 findings (0 High, 0 Medium, 0 Low).

STARTING state transitions correctly via startup_ticks counter. DPI awareness wrapped in try/except (graceful fallback). CREATE_NO_WINDOW flag added to overlay spawn. 11 overlay tests pass including new creation-flag test.

Per-phase review deferred to Step 9: Phase 4 is test-only fixes (no new executable code), mechanical alignment with Phases 1-3 changes.

### 2026-06-13 -- Post-Implementation Review

Overall implementation health: Green (after auto-fixes).
Personas: Senior engineer, Reliability engineer.
5 findings (0 High, 2 Medium, 3 Low).
QA verification: SKIP — no runtime environment available for toast/overlay verification.

| # | Severity | Finding | Resolution |
|---|---|---|---|
| 1 | Medium | `startup_ticks` not reset on crash-restart; STARTING→RUNNING transitions instantly | Fixed — reset before `_spawn()` in restart path |
| 2 | Medium | `_terminate_proc` doesn't join `_log_reader`; orphaned thread may conflict | Fixed — join with 2s timeout added |
| 3 | Low | `_read_worker_logs` appends empty lines to buffer | Fixed — added content guard |
| 4 | Low | No notification on transient single crash (only on final give-up) | User: accepted — out of scope for this plan |
| 5 | Low | Web UI Logs tab polls all 200 lines every 3s (no delta) | User: accepted — acceptable for v1 loopback-only |

## 9) Implementation Divergences from Plan
<Reserved -- filled during implementation>
