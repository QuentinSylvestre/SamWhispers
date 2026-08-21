# Supervisor Duplicate Boot Race Fix

> **Date**: 2026-08-21
> **Status**: Exploring
> **Scope**: Fix three related defects in supervisor startup/shutdown that cause duplicate instances at boot and silent web-server misconfiguration

---

## Intent

### Problem statement & desired outcomes

At Windows boot, two supervisor instances start simultaneously because the Startup-folder shortcut
fires twice (or the prior session's process hasn't fully exited). Both non-foreground parent
processes call `is_running()`, which acquires-then-immediately-releases the OS lock, before either
foreground child has acquired it — so both parents see `is_running()=False` and both spawn a
`--foreground` child via `_relaunch_detached()`. The `msvcrt.locking` lock IS exclusive, so one
foreground child wins the lock and the other exits — but only after both have started their worker,
overlay, and uvicorn thread. The child that loses the lock exits cleanly, but its uvicorn thread
has already raced to bind port 7891. The surviving supervisor's tray "Open settings" click either
opens a browser that gets connection refused (if the port was stolen) or works normally (if it won
the port) — but runtime.json, supervisor.pid, and the tray state are inconsistent.

A secondary defect: `serve()` returns a non-None handle before the uvicorn thread has attempted
the bind, so `write_metadata()` writes `web_enabled=True` even if the port bind subsequently fails
in the thread. The stop/restart commands then trust a `web_enabled=True` flag that points at a
dead server.

A third defect: `supervisor.pid` is never deleted on normal exit, leaving a stale PID that can
mislead `_do_stop()`'s fallback path on the next run.

Desired outcomes: exactly one supervisor runs after boot, the tray "Open settings" works, and
`runtime.json`/`supervisor.pid` accurately reflect the running process.

### Success criteria

1. Starting two non-foreground supervisor processes simultaneously results in exactly one running
   supervisor, one running worker, and one running overlay after both have settled.
2. `runtime.json` has `web_enabled=True` and a valid `web_port` only when the uvicorn server has
   actually bound the port successfully.
3. `supervisor.pid` does not exist on disk after a clean `samwhispers stop`.
4. The tray restart flow (old supervisor releases lock → `_relaunch_detached()` → new child starts)
   is unaffected by the changes.
5. New tests cover: concurrent `--foreground` spawn race, web_enabled accuracy under port conflict,
   supervisor.pid absent after clean exit.

### Scope boundaries & non-goals

**In scope:**
- Add `is_running()` guard to the `--foreground` path in `supervisor.main()`.
- Poll `server.started` (with 2s timeout, 50ms interval) before writing `runtime.json`, so
  `web_enabled` reflects actual bind success. Mark `web_enabled=False` if thread dies or timeout
  expires without `server.started=True`.
- Delete `supervisor.pid` in the `finally` block of `supervisor.main()`, alongside
  `delete_metadata()`.
- New tests for all three fixes.

**Out of scope:**
- Changing the `supervisor.lock` file format or lock mechanism.
- Changing the autostart shortcut or how it launches the supervisor.
- Rewriting `_relaunch_detached()` or the parent/child handoff protocol.
- Fixing the duplicate-start root cause at the Windows Startup folder level.

---

## Exploration Discovery

<!-- Transient: /qplan folds these into the planning sections and removes this section. -->

### 4. Existing patterns & constraints

- `singleinstance.py:24–41`: `InstanceLock.acquire()` opens `supervisor.lock` with `O_RDWR|O_CREAT`
  and calls `msvcrt.locking(fd, LK_NBLCK, 1)`. The lock IS exclusive between concurrent processes
  (probe: 5/5 runs, 1/5 children succeeded). The file stays 0 bytes — no content ever written.
- `singleinstance.py:86–90`: `is_running()` acquires-then-immediately-releases. It holds no lock
  after returning. This is the window exploited by the race.
- `supervisor.py:552–566`: the `if not args.foreground:` branch calls `is_running()` then
  `_relaunch_detached()`. The `--foreground` branch (line ~578) skips `is_running()` entirely and
  goes straight to `lock.acquire()`.
- `supervisor.py:605–625`: `web_handle = _start_web(...)` called before `write_metadata()`.
  `serve()` returns a non-None handle before the uvicorn daemon thread has bound the port. Probe
  confirmed: with port pre-bound, `server.started=False` and thread dies after ~1-2s.
- `supervisor.py:661–668` (`finally` block): calls `supervisor.shutdown()`, `web_handle.shutdown()`,
  `delete_metadata()`, `lock.release()`. Does NOT delete `supervisor.pid`.
- `runtime.py:118–143`: `write_metadata()` does an atomic temp-then-`os.replace()` write. Last
  writer wins under concurrent writes.
- `tray.py:96`: "Open settings" menu item is only added when `settings_url` is non-None (which
  requires `web_handle` to be non-None at call time in `main()`).
- `plans/done/260706-1203_FULL_RESTART_LIFECYCLE.md` row 3: sequential handoff design — new child
  cannot start until old one releases the lock. Tray restart path is safe: `finally` block (incl.
  `lock.release()`) runs before `_relaunch_detached()` is called post-loop.
- `plans/done/260706-1203_FULL_RESTART_LIFECYCLE.md` row 6: PID in separate file from lock —
  deliberate, to avoid Windows lock-byte conflicts.
- `tests/test_singleinstance.py`: 3 tests, all sequential in-process. No concurrent-process test.
  `write_pid()` is not tested anywhere.
- AGENTS.md: tests use pytest (`python -m pytest tests/ -v`); lint with ruff; type-check with mypy.

### 5. Risks & mitigations

- **TOCTOU on the new `is_running()` guard**: after `is_running()` returns False and before
  `lock.acquire()` runs, another process could win the lock. Mitigation: `lock.acquire()` is the
  authoritative gate; `is_running()` is only an early-exit that fires the same `log.error` path
  sooner. The real lock still excludes the second process. Identical semantics to the existing
  non-foreground guard.
- **Poll delays tray restart startup by up to 2s**: the new `server.started` poll adds up to 2s
  before `runtime.json` is written. Mitigation: the tray loop starts before `write_metadata()` is
  called (tray is passed `settings_url` and starts independently), so the tray icon appears
  immediately; only the stop/restart commands that read `runtime.json` are delayed, and 2s is
  imperceptible for a background daemon.
- **`supervisor.pid` unlink fails on crash**: crash before `write_pid()` means the file doesn't
  exist; `missing_ok=True` handles this. Crash after `write_pid()` but before `finally`: the file
  persists (same as today), but `validate_metadata()` in the stop flow already handles stale PIDs
  via `is_pid_alive()` + `is_samwhispers_process()`.
- **Subprocess-based test flakiness**: the concurrent-spawn test must spawn real OS processes and
  race them. Mitigation: use a `go_time = time.time() + 0.3` synchronization pattern (as used in
  the probe scripts) to narrow the window. Mark the test with `pytest.mark.slow` if needed.

### 6. Resolved decisions

- Q1: Where to fix Layer 1 (duplicate spawn)? — A: option A, add `is_running()` to `--foreground`
  path — Decision: one-line guard in `supervisor.main()` before `lock.acquire()` on the
  `--foreground` branch. Symmetric with non-foreground guard.
- Q2: Fix Layer 2 (web_enabled accuracy)? — A: yes — Decision: poll `server.started` after
  `serve()`, 2s timeout / 50ms interval; if thread dies or timeout expires without True, mark
  `web_enabled=False`.
- Q3: Fix `supervisor.pid` not cleaned up on exit? — A: yes — Decision: add
  `pid_path().unlink(missing_ok=True)` to `finally` block alongside `delete_metadata()`.
- Q4: Poll timeout and fallback for `server.started`? — A: 2s timeout, conservative (False)
  fallback — Decision: if `server.started` is not True within 2s or thread dies, write
  `web_enabled=False, web_port=None, csrf_token=None`.

### 7. Open items

None.

### 8. Recommended approach

Three self-contained changes, each in a separate phase:

**Phase 1 — `is_running()` guard on `--foreground` path (`supervisor.py`)**

In `main()`, immediately before the `from samwhispers.singleinstance import InstanceLock` line on
the `--foreground` branch, add:

```python
from samwhispers.singleinstance import is_running
if is_running():
    log.error("Another SamWhispers instance is already running; exiting.")
    return
```

This mirrors the existing guard on the non-foreground branch (lines ~553–556) exactly. The only
difference is no `webbrowser.open()` fallback — the foreground path is not an interactive launch
so opening a browser is inappropriate.

**Phase 2 — `web_enabled` accuracy (`supervisor.py` + `webserver.py`)**

After `web_handle = _start_web(...)` and before `write_metadata(...)`, add a confirmation step:

```python
if web_handle is not None:
    # Wait for uvicorn to actually bind before advertising the port
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        if not web_handle.thread.is_alive():
            web_handle = None  # thread died — bind failed
            break
        if web_handle.server.started:
            break
        time.sleep(0.05)
    else:
        # Timeout — treat as failure (conservative)
        web_handle = None
```

`settings_url` must be recomputed after this block (it currently reads `web_handle.url` before the
poll). The `write_metadata` call is already after this point, so no other changes needed to the
metadata write path.

**Phase 3 — `supervisor.pid` cleanup (`supervisor.py`)**

In the `finally` block, after `delete_metadata()`, add:

```python
from samwhispers.singleinstance import pid_path
pid_path().unlink(missing_ok=True)
```

**Tests** (Phase 4):

- `test_singleinstance.py`: add `test_concurrent_foreground_only_one_wins()` — spawn two
  subprocesses that both call `InstanceLock().acquire()` simultaneously; assert exactly one returns
  True. (The lock is already exclusive; this is a regression guard.)
- `tests/test_supervisor.py` or `tests/test_runtime.py`: add test for `web_enabled=False` when
  port is pre-bound.
- `tests/test_singleinstance.py` or integration test: confirm `supervisor.pid` is absent after
  `supervisor.shutdown()`.

### 9. QA environment

- Live test: `samwhispers stop` then observe `Get-Process pythonw` to confirm no surviving
  instances, then `samwhispers` (non-foreground) and confirm single instance.
- Simulate the boot race: open two PowerShell windows, simultaneously run the non-foreground launch
  command in both, confirm only one supervisor remains after ~5s.
- Port conflict test: `netstat -ano | findstr :7891` before and after launch to confirm the port
  is bound exactly once.
- `supervisor.pid` absence: after `samwhispers stop`, confirm
  `Test-Path $env:LOCALAPPDATA\samwhispers\supervisor.pid` returns False.
- All existing tests: `python -m pytest tests/ -v`.

## Harness Improvement Opportunities

- Probe gate fires after Step 1.5 but the SKILL.md says "run every probe on those lists" before
  the interview — the probes and interview were interleaved rather than batch-probed first. No
  material cost here (probes were fast), but the skill text reads as batch-then-interview.
  cost: ~1 extra turn reordering — suggested change: clarify in SKILL.md whether probes must
  all complete before Q1 or can interleave with interview questions.
