# Full Restart Lifecycle (start/stop/restart)

> **Date**: 2026-06-13
> **Status**: All 3 phases COMPLETE
> **Last Updated**: 2026-07-06 12:03
> **Scope**: Add full supervisor restart/stop commands to CLI, tray, and web UI
> **Estimated effort**: 2-4 hours

---

## Intent

### Problem statement & desired outcomes

Currently there's no way to do a full restart of SamWhispers (supervisor + whisper-server + worker) without manually killing and relaunching processes. When code changes are made (like adding server flags), only a worker restart is available, which doesn't reload the supervisor or whisper-server. Users need `start`, `stop`, and `restart` commands accessible from the CLI, tray icon, and web UI.

### Success criteria

1. `samwhispers stop` gracefully shuts down the running instance (HTTP first, SIGTERM/kill fallback)
2. `samwhispers restart` performs a full re-exec (new supervisor replaces old, picking up code changes)
3. Tray menu has "Restart SamWhispers" item that triggers a full re-exec
4. Web UI has a full-restart button/endpoint (`POST /api/supervisor/restart`)
5. `samwhispers` (bare) and `samwhispers start` both launch as before (backward compatible)
6. Works with `--no-web` (PID-based fallback)

### Scope boundaries & non-goals

- **In scope**: CLI subcommands (start/stop/restart), tray menu item, web API endpoints, PID file for fallback
- **Out of scope**: Hot-reload without process restart, remote (non-loopback) control, auth on the endpoints

## 1) Current State

- `supervisor.py:~283` — `_relaunch_detached(args)` spawns a new supervisor; requires full argparse namespace
- `supervisor.py:~85` — `WorkerSupervisor.__init__` only stores `config_path` and `verbose`, not full launch flags
- `supervisor.py:~108` — `restart()` only restarts the worker, not whisper-server/supervisor
- `singleinstance.py:30-45` — `InstanceLock.acquire()` takes OS file lock (Windows: 1 byte at offset 0 via msvcrt); no PID written
- `tray.py:95-105` — menu: status, SEPARATOR, Open settings, Pause, Restart worker, SEPARATOR, Quit
- `webserver.py:197-207` — `POST /api/worker/{action}` for pause/resume/restart (worker only)
- `__main__.py:19-23` — dispatch only recognizes `worker` subcommand; bare = supervisor

## 2) Goal

Add `start`/`stop`/`restart` CLI subcommands, web API endpoints for supervisor shutdown/restart, a tray menu item for full restart, and PID-file fallback for `--no-web` scenarios — enabling full process lifecycle management from all three surfaces.

## 3) Design Decisions

| Decision | Choice | Alternatives considered | Rationale |
|---|---|---|---|
| IPC mechanism | HTTP via existing web server | Named pipe, Unix socket, signal-only | Web server already exists, no new deps |
| Fallback for --no-web | Separate PID file + process kill | No fallback | Completeness — must work headless |
| Re-exec strategy | Old exits first, new starts after (sequential) | Spawn new then exit old | Lock is exclusive — new can't start while old holds it; sequential avoids race |
| CLI UX | Subcommands: start/stop/restart | Flags (--stop, --restart) | Subcommands are clearer and standard |
| Tray items | Keep both "Restart worker" and "Restart SamWhispers" | Replace worker restart | Different use cases: worker is fast, full restart reloads everything |
| PID storage | Separate PID file (not in lock file) | Write PID into lock file | Windows locks 1 byte at offset 0 — writing PID to same fd conflicts with the lock region |
| Windows graceful stop | HTTP endpoint (only graceful path); PID fallback is force-kill only | SIGTERM | Windows `os.kill(pid, SIGTERM)` = `TerminateProcess` = force kill; no graceful signal path |
| Shutdown trigger from endpoint | Set an event that the main thread checks, not `sys.exit()` | `sys.exit()` in route | `sys.exit()` in a uvicorn thread is caught by uvicorn, won't propagate to supervisor |
| Store launch args | Supervisor stores full args dict at init for re-exec | Reconstruct from config | `_relaunch_detached` needs all flags; storing them is simpler than re-parsing |

## 4) External Dependencies & Costs

### Cost impact

None — code-only change, no new infrastructure or services.

## 5) Implementation Phases

### Phase 1: PID file, launch-args storage, and supervisor endpoints [QA]

**Goal**: Write PID to a separate file; store launch args in supervisor for re-exec; add shutdown/restart endpoints that coordinate with the main thread.

**File scope**: `singleinstance.py`, `webserver.py`, `supervisor.py`

**Changes**:
- `singleinstance.py`: Add `write_pid()` (writes `os.getpid()` to `<data-dir>/supervisor.pid`) and `read_pid()` functions. Separate file from lock file to avoid Windows lock-byte conflicts.
- `supervisor.py`:
  - Store full launch args (config, verbose, no_tray, no_web, web_port) in `WorkerSupervisor.__init__` or a module-level store accessible to `relaunch()`
  - Add `relaunch()` method: sets a `_relaunch_requested` event, which the main thread checks after tray/headless loop exits → then spawns new detached process and returns (old process exits normally, releasing lock; new process acquires lock)
  - Add `request_shutdown()` method: sets `_shutdown_requested` event that the main thread checks
- `webserver.py`:
  - `POST /api/supervisor/shutdown` — calls `supervisor.request_shutdown()`, returns `{"shutting_down": true}` immediately
  - `POST /api/supervisor/restart` — calls `supervisor.relaunch()`, returns `{"restarting": true}` immediately
- Call `write_pid()` after lock is acquired in `main()`

**Exit criteria**:
- [x] PID file created at startup, readable via `read_pid()`
- [x] `POST /api/supervisor/shutdown` triggers clean shutdown (main thread exits)
- [x] `POST /api/supervisor/restart` triggers re-exec (old exits, new starts)
- [x] Launch args are preserved for re-exec fidelity

Implementation (2026-06-13, code: d6a2f65)
Added `write_pid()` and `read_pid()` to `singleinstance.py` for PID file management at `<data-dir>/supervisor.pid`. Updated `supervisor.py` to store launch args in a module-level `_launch_args` dict at startup, added `request_shutdown()` and `request_relaunch()` event-setting methods to `WorkerSupervisor`, refactored `_relaunch_detached()` to work from the stored args dict, and rewired `main()` to pass a `stop_callback` to the web server and check relaunch/shutdown events after the tray/headless loop exits. Added `POST /api/supervisor/shutdown` and `POST /api/supervisor/restart` endpoints to `webserver.py`.

### Phase 2: CLI subcommands [QA] [P:3]

**Goal**: Add `start`/`stop`/`restart` subcommands with user feedback messages.

**File scope**: `__main__.py`

**Changes**:
- Add subcommand dispatch: `start` (default), `stop`, `restart`
- `stop`:
  1. Try HTTP `POST /api/supervisor/shutdown` → print "Stopping SamWhispers..." then poll until process exits or 5s
  2. If no web server (connection refused): read PID file, force-kill process (Windows: `taskkill /F /PID`; POSIX: `SIGTERM` then `SIGKILL` after 5s)
  3. Print "SamWhispers stopped." on success; "SamWhispers is not running." if nothing to stop
- `restart`:
  1. Try HTTP `POST /api/supervisor/restart` → print "Restarting SamWhispers..."
  2. If no web server: `stop` then `start`
  3. Print "SamWhispers restarted." on success
- `start`: same as current bare invocation, passes all flags through
- Bare `samwhispers` (no subcommand) dispatches to `start` for backward compat
- All subcommands show `--help` text

**Exit criteria**:
- [x] `samwhispers stop` prints feedback and stops a running instance
- [x] `samwhispers stop` prints "not running" when nothing to stop
- [x] `samwhispers restart` prints feedback and performs full restart
- [x] `samwhispers restart` when not running just starts it
- [x] `samwhispers` and `samwhispers start` both launch normally with all existing flags
- [x] Works when web server is disabled (PID fallback with force-kill)
- [x] `--help` shows start/stop/restart subcommands

Implementation (2026-06-13, code: d5c276e)
Rewrote `__main__.py` to use argparse subparsers exposing `start` (default, all supervisor flags), `stop` (HTTP graceful shutdown with PID force-kill fallback, user feedback messages), and `restart` (HTTP restart endpoint with stop+start fallback). Bare `samwhispers` invocation remains backward-compatible by dispatching to supervisor_main() when no subcommand is given. The `worker` internal subcommand is preserved.

### Phase 3: Tray and web UI [QA] [P:2]

**Goal**: Add "Restart SamWhispers" to the tray menu and a restart button in the web UI with appropriate feedback.

**File scope**: `tray.py`, `web/index.html`

**Changes**:
- `tray.py`: Add "Restart SamWhispers" menu item before the final SEPARATOR (after "Restart worker"), calling `supervisor.relaunch()`. Show a desktop notification "SamWhispers is restarting..." before the re-exec so the user knows it's intentional.
- `web/index.html`: Add "Restart SamWhispers" button in the General page actions bar, hitting `POST /api/supervisor/restart`. Show a banner "Restarting SamWhispers..." and poll `/api/status` until the new instance responds, then reload.

**Exit criteria**:
- [x] Tray menu shows "Restart SamWhispers" and clicking it restarts with notification
- [x] Web UI button triggers full restart with visual feedback during the gap
- [x] Update README.md Usage section with start/stop/restart commands

Implementation (2026-06-13, code: 831df54)
Added "Restart SamWhispers" menu item to tray.py that calls request_relaunch() with a desktop notification before stopping the icon, added a "Restart SamWhispers" button to the web UI General page that POSTs to /api/supervisor/restart and polls until the new instance responds then reloads, and documented start/stop/restart subcommands in the README Usage section.

## 6) Risk Assessment

| Risk | Impact | Mitigation |
|---|---|---|
| Port race on restart | New instance can't bind | Sequential: old exits fully (releases lock + port), new starts after |
| Windows has no graceful signal | Can't SIGTERM gracefully | HTTP is the graceful path; PID fallback is explicitly force-kill |
| Stale PID file after crash | Could kill wrong process | Validate PID is a running samwhispers process before killing (check cmdline) |
| Web endpoint response truncated on shutdown | Client sees error | Return response first, schedule shutdown via event after 200ms delay |

## 7) Verification

- `samwhispers stop` from CLI while running → prints feedback, process exits cleanly
- `samwhispers stop` when not running → prints "SamWhispers is not running."
- `samwhispers restart` → prints feedback, new PID, all surfaces functional
- `samwhispers restart` when not running → starts it
- Tray "Restart SamWhispers" → notification shown, app restarts, tray re-appears
- Web UI restart button → banner shown, page reconnects after restart
- `--no-web` mode: stop/restart work via PID fallback (force-kill)
- All existing flags (`-v`, `-c`, `-f`, `--no-tray`, `--no-web`, `--web-port`) work with `start`

## 8) Documentation Updates

| Document | Update needed | Phase |
|---|---|---|
| `README.md` | Add start/stop/restart CLI usage in the Usage section | 3 |

## 9) Implementation Divergences from Plan

<Reserved — filled during implementation>


## Review Log

### 2026-06-13 — Plan Review (Architect + Senior Engineer + End-user Advocate)

17 findings (5 High, 7 Medium, 5 Low). 12 auto-resolved.

| # | Severity | Finding (one line) | Resolution (one line) |
|---|---|---|---|
| 1 | High | Lock race: new can't acquire lock while old holds it | Resolved — changed to sequential strategy (old exits first, new starts after) |
| 2 | High | Windows SIGTERM = TerminateProcess, not graceful | Resolved — HTTP is graceful path; PID fallback explicitly documented as force-kill only |
| 3 | High | `_relaunch_detached` needs full args but supervisor doesn't store them | Resolved — added design decision to store full launch args at init |
| 4 | High | PID write conflicts with Windows 1-byte lock on same fd | Resolved — separate PID file, not in lock file |
| 5 | High | `sys.exit()` in uvicorn route won't propagate to main thread | Resolved — use event-based signaling to main thread instead |
| 6 | High | No CLI feedback messages for stop/restart | Resolved — Phase 2 now specifies all user-facing messages |
| 7 | High | No error message when stopping a non-running instance | Resolved — Phase 2 specifies "SamWhispers is not running." |
| 8 | Medium | No shutdown ordering guarantee for HTTP response | Resolved — risk table updated with 200ms delay mechanism |
| 9 | Medium | Stale PID could kill wrong process | Resolved — risk table: validate PID is samwhispers before killing |
| 10 | Medium | Web UI has no reconnection UX | Resolved — Phase 3 specifies polling + banner |
| 11 | Medium | `restart` when not running undefined | Resolved — Phase 2: just starts it |
| 12 | Medium | `start` must accept all existing flags | Resolved — Phase 2 specifies flag passthrough |
| 13 | Medium | No test files specified | Noted — tests will be added for new functions per AGENTS.md |
| 14 | Medium | Phase parallelism annotation could be clearer | Noted — P:2/P:3 means phases 2&3 are parallel (both depend on Phase 1) |
| 15 | Low | Tray restart has no immediate feedback | Resolved — Phase 3: desktop notification before re-exec |
| 16 | Low | No --help text mentioned | Resolved — Phase 2 exit criteria includes --help |
| 17 | Low | Approximate line numbers in Current State | Resolved — prefixed with ~ to indicate approximate |
