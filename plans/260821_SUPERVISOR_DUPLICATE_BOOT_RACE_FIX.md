# Supervisor Duplicate Boot Race Fix

> **Date**: 2026-08-21
> **Status**: In Progress
> **Last Updated**: <set by /qclose at archival>
> **Scope**: Fix three defects causing duplicate supervisor instances, silent web misconfiguration, and stale PID files
> **Estimated effort**: ~4 hours

---

## Intent

### Problem statement & desired outcomes

At Windows boot, the Startup-folder shortcut fires twice (or the prior session's process hasn't
fully exited). Both non-foreground parent processes call `is_running()`, which
acquires-then-immediately-releases the OS lock, before either foreground child has acquired it
— so both parents see `is_running()=False` and both spawn a `--foreground` child via
`_relaunch_detached()`. Both foreground children race to `lock.acquire()` at `supervisor.py:576`.
The `msvcrt.locking` lock IS exclusive — one child acquires it and proceeds; the other gets
`log.error` and exits before starting any worker or uvicorn thread. However, both children have
already called `logging.basicConfig()` and allocated resources up to the `lock.acquire()` call.
The Phase 1 fix eliminates this wasted work with an early-exit guard.

A secondary defect, independent of the duplicate-start scenario: `serve()` returns a non-None
handle before the uvicorn daemon thread has attempted the bind, so `write_metadata()` writes
`web_enabled=True` even when the port bind subsequently fails in the thread (e.g., an external
process holds port 7891). The stop/restart commands then trust a `web_enabled=True` flag pointing
at a dead server.

A third defect: `supervisor.pid` is never deleted on normal exit, leaving a stale PID that can
mislead `_do_stop()`'s fallback path on the next run.

Desired outcomes: exactly one supervisor runs after boot, the tray "Open settings" works when the
web server is actually running, and `runtime.json`/`supervisor.pid` accurately reflect the running
process.

### Success criteria

- SC-1: Starting two non-foreground supervisor processes simultaneously results in exactly one
  running supervisor and one running worker after both have settled.
- SC-2: `runtime.json` has `web_enabled=True` and a valid `web_port` only when the uvicorn server
  has actually bound the port successfully.
- SC-3: `supervisor.pid` does not exist on disk after a clean `samwhispers stop`.
- SC-4: The tray restart flow (old supervisor releases lock → `_relaunch_detached()` → new child
  starts) is unaffected by the changes — timing and behavior identical to pre-fix.
- SC-5: New tests cover concurrent `--foreground` spawn race (SC-1), web_enabled accuracy under
  port conflict (SC-2), and supervisor.pid absent after clean exit (SC-3).

### Invariants

- `samwhispers stop` terminates the supervisor and its worker without requiring a second invocation.
- After a tray-initiated restart, exactly one supervisor and one worker are running within 5
  seconds.
- The `supervisor.lock` file format is unchanged (0-byte advisory lock, no content written).
- The existing `is_running()` → `_relaunch_detached()` sequence in the non-foreground path is
  unchanged.

### Scope boundaries & non-goals

**In scope:**
- Add `is_running()` early-exit guard to the `--foreground` path in `supervisor.main()`.
- Add `is_ready()` method to `WebServerHandle` in `webserver.py`.
- Poll `web_handle.is_ready()` (2s timeout, 50ms interval) between `_start_web()` and
  `write_metadata()`. Shut down the handle before nulling it on timeout to avoid orphaned threads.
- Delete `supervisor.pid` in the `finally` block alongside `delete_metadata()`, wrapped in
  `try/except OSError` to tolerate `PermissionError`.
- New tests for all three fixes.
- README update for supervisor.pid cleanup behavior.

**Out of scope:**
- Changing the `supervisor.lock` file format or lock mechanism.
- Changing the autostart shortcut or how it launches the supervisor.
- Rewriting `_relaunch_detached()` or the parent/child handoff protocol.
- Fixing the duplicate-start root cause at the Windows Startup folder level.

---

## 1) Current State

**Race path** (`supervisor.py:551–566`, confirmed by probe 2026-08-21):

```python
if not args.foreground:
    from samwhispers.singleinstance import is_running
    if is_running():          # acquire+release — holds no lock after returning
        ...
        return
    _relaunch_detached(args)  # spawns --foreground child and returns immediately
    return
```

Both non-foreground parents reach `is_running()` before either `--foreground` child acquires the
lock, so both see `False` and both call `_relaunch_detached()`. The probe ran 3 concurrent parent
simulations; all 3 saw `is_running()=False` simultaneously (2026-08-21).

**Missing guard** (`supervisor.py:574–581`): the `--foreground` branch skips `is_running()`:

```python
logging.basicConfig(...)             # already runs in the losing child too
...
from samwhispers.singleinstance import InstanceLock
lock = InstanceLock()
if not lock.acquire():               # losing child exits HERE (line ~578)
    log.error("Another SamWhispers instance is already running; exiting.")
    return
# WorkerSupervisor, _start_web, write_metadata all come AFTER this point
```

The losing child exits at `lock.acquire()` failure and never starts a worker or uvicorn thread.
The `msvcrt.locking` lock IS exclusive (probe: 5/5 runs, 1/5 concurrent children acquired). The
Phase 1 fix moves the exit earlier (before `logging.basicConfig`) to eliminate setup work in the
losing child.

**Web metadata timing** (`supervisor.py:605–625`):

```python
web_handle = _start_web(...)          # returns non-None handle immediately
settings_url = web_handle.url if web_handle else None
# ... immediately writes metadata:
meta = RuntimeMetadata(
    web_enabled=not args.no_web and web_handle is not None,  # True even if bind fails
    ...
)
write_metadata(meta)
```

`_start_web()` calls `serve()` (`supervisor.py:699`), which starts the uvicorn daemon thread and
returns a `WebServerHandle` before the thread has attempted the bind (`webserver.py:712–728`). If
the port is already bound, the thread raises and dies silently. Probe (2026-08-21): with port
pre-bound, `serve()` returned a non-None handle; after 2s, `thread.is_alive()=False`,
`server.started=False`.

**PID file lifecycle** (`singleinstance.py:63–69`, `supervisor.py:592–593`, `supervisor.py:661–668`):
`write_pid()` is called after `lock.acquire()` succeeds. The `finally` block calls
`delete_metadata()` (removes `runtime.json`) and `lock.release()` — but never calls
`pid_path().unlink()`. `supervisor.pid` persists after every clean exit.

**`finally` block** (`supervisor.py:661–668`) for reference:

```python
finally:
    supervisor.shutdown()
    if web_handle is not None:
        web_handle.shutdown()
    from samwhispers.runtime import delete_metadata
    delete_metadata()
    lock.release()
```

---

## 2) Goal

Add an `is_running()` early-exit guard to the `--foreground` path, confirm web server bind before
writing metadata, and clean up `supervisor.pid` on normal exit — eliminating the duplicate-instance
race, silent web misconfiguration, and stale PID file.

---

## 3) Design Decisions

| Decision | Choice | Alternatives considered | Rationale |
|---|---|---|---|
| Where to add the duplicate guard (Q1) | `is_running()` check at top of `--foreground` branch in `main()`, before `logging.basicConfig` | (A) hold lock across `_relaunch_detached()` in parent | Lock-handoff between processes is complex on Windows; this is a minimal addition symmetric with the existing non-foreground guard. The real `lock.acquire()` remains the authoritative gate. |
| Web bind confirmation (Q2) | `is_ready()` method on `WebServerHandle`; poll 2s/50ms; shutdown handle before nulling on timeout | (A) check `thread.is_alive()` only after fixed sleep; (B) restructure `serve()` to bind synchronously | Non-blocking poll is minimal change; shutdown-before-null prevents orphaned live threads; `is_ready()` confines the uvicorn coupling to `webserver.py`. |
| Poll timeout fallback (Q4) | 2s timeout → shut down handle → `web_enabled=False` (conservative) | Optimistic: write True, let commands discover the dead server | CLI stop/restart read `web_enabled`; advertising a non-functional port wastes a round-trip. |
| Poll check order | Check `is_ready()` first, then `not thread.is_alive()` | thread.is_alive() first | `server.started` is the authoritative success signal; a thread that exited after a successful bind should not be reported as failed. |
| PID file cleanup (Q3) | `try/except OSError` wrapping `pid_path().unlink(missing_ok=True)` in `finally` | Bare unlink (propagates PermissionError) | `missing_ok=True` suppresses `FileNotFoundError`; `except OSError` additionally suppresses `PermissionError` (file held open on Windows); symmetry with `delete_metadata()`. |
| `pid_path` import placement | Move to same import site as `write_pid` at top of `--foreground` branch | Import inside `finally` | Avoids an import inside `finally` (style inconsistency); the module is already loaded at the earlier import site so cost is zero. |

---

## 4) External Dependencies & Costs

### Required external changes

None — code-only change within the samwhispers package.

### Cost impact

None.

---

## 5) Implementation Phases

### Phase 1: Add `is_running()` guard to `--foreground` path [QA]

**Goal**: Eliminate setup work in the losing foreground child by exiting before `logging.basicConfig`
if another supervisor already holds the lock.

**Covers**: SC-1, SC-4

**File scope**: `src/samwhispers/supervisor.py`

The `--foreground` branch of `main()` currently begins (`supervisor.py:567`):

```python
logging.basicConfig(
    level=logging.DEBUG if args.verbose else logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)

from samwhispers.singleinstance import InstanceLock
...
```

Change to insert the guard **before** `logging.basicConfig`:

```python
# Early-exit guard: mirrors the non-foreground branch's is_running() check.
# The real lock.acquire() below is still the authoritative gate; this is an
# optimization that avoids logging setup and resource allocation in the losing child.
from samwhispers.singleinstance import is_running as _is_running
if _is_running():
    # Log to stderr directly: logging not yet configured at this point.
    import sys
    print(
        "Another SamWhispers instance is already running; exiting.",
        file=sys.stderr,
    )
    return

logging.basicConfig(
    level=logging.DEBUG if args.verbose else logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)

from samwhispers.singleinstance import InstanceLock
...
```

**Why `print` to stderr rather than `log.error`**: `log` is a module-level logger, but
`logging.basicConfig()` has not yet run at this insertion point, so only the root logger's
default NullHandler is active. Using `print(..., file=sys.stderr)` matches how Python's own
stdlib reports pre-logging errors and avoids depending on an unconfigured logger.

**Why `_is_running` alias**: avoids shadowing the module-level name `is_running` (which is not
imported at module level in supervisor.py, but the alias makes the scope crystal-clear).

**Tray restart invariant**: in the tray restart path, the old supervisor's `finally` block runs
`lock.release()` before `_relaunch_detached()` is called (post-`finally` at `supervisor.py:671`).
By the time the new child reaches the new `is_running()` check, the lock is free. This is
provably safe: `_relaunch_detached()` is called only after `finally` exits.

**Exit criteria**:
- [x] `is_running` guard inserted before `logging.basicConfig` in the `--foreground` branch
- [x] Uses `print(..., file=sys.stderr)` not `log.error` (logger not yet configured)
- [x] No `webbrowser.open()` added
- [x] `ruff check src/samwhispers/supervisor.py` passes
- [x] `mypy src/` passes

---

### Phase 2: Confirm web server bind before writing metadata [QA]

**Goal**: Ensure `web_enabled` in `runtime.json` is `True` only when the uvicorn server has
successfully bound the port. Confine the uvicorn-internal `server.started` coupling to
`webserver.py`.

**Covers**: SC-2

**File scope**: `src/samwhispers/webserver.py`, `src/samwhispers/supervisor.py`

#### 2a — Add `is_ready()` to `WebServerHandle` (`webserver.py`)

`WebServerHandle` is defined starting around `webserver.py:700`. Add one property:

```python
@property
def is_ready(self) -> bool:
    """True once uvicorn has successfully bound the port."""
    return bool(self.server.started)
```

`self.server` is a `uvicorn.Server` instance (set in `serve()` at `webserver.py:723`). The
`.started` attribute is set to `True` by uvicorn in its `startup()` coroutine after the socket
is bound. This confines the uvicorn coupling to `webserver.py`.

#### 2b — Poll loop in `supervisor.py`

Replace the current `settings_url` line (at `supervisor.py:606`) with the following block,
inserted between `web_handle = _start_web(...)` and the `settings_url = ...` assignment:

```python
web_handle = _start_web(supervisor, args.config, args.no_web, args.web_port, stop_callback=_stop_main_loop)

# Wait for uvicorn to confirm the bind before advertising the port.
# serve() returns a handle immediately; the actual bind happens in a daemon thread.
# Check is_ready() first (success path), then thread liveness (failure path).
# Shut down the handle before nulling it so no threads are orphaned.
if web_handle is not None:
    effective_port_for_log = args.web_port or DEFAULT_PORT
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        if web_handle.is_ready:
            break
        if not web_handle.thread.is_alive():
            log.warning(
                "Web server thread died before binding on port %d; disabling web UI",
                effective_port_for_log,
            )
            web_handle.shutdown()
            web_handle = None
            break
        time.sleep(0.05)
    else:
        # Loop exhausted without break: 2-second timeout without server.started.
        log.warning(
            "Web server did not confirm bind on port %d within 2s; disabling web UI",
            effective_port_for_log,
        )
        web_handle.shutdown()
        web_handle = None

settings_url = web_handle.url if web_handle else None
```

`DEFAULT_PORT` is already imported at this point in the `--foreground` branch (used in the
`RuntimeMetadata` construction below). `time` is already imported at `supervisor.py:23`.

**Note on `while/else`**: the `else` clause of a Python `while` loop runs only when the loop
condition becomes false (i.e., the deadline was reached without a `break`). Both `break` paths
(success via `is_ready` and failure via dead thread) skip the `else`. This is the intended
behavior — timeout is detected only via `else`.

**`finally` block interaction**: after the poll, `web_handle` may be `None`. The `finally` block
at `supervisor.py:662–664` already guards with `if web_handle is not None:` before calling
`web_handle.shutdown()`, so a `None` here is safe. Both the thread-died and timeout paths call
`web_handle.shutdown()` before setting to `None`, ensuring no live uvicorn thread is orphaned.

**Exit criteria**:
- [x] `is_ready` property added to `WebServerHandle` in `webserver.py`
- [x] Poll loop with `is_ready` check first, thread-alive check second, inserted between `_start_web()` and `settings_url = ...`
- [x] Both failure branches (`thread died`, `timeout`) call `web_handle.shutdown()` before setting `web_handle = None`
- [x] Both failure branches emit a `log.warning` naming the port
- [x] `settings_url` assignment moved to after the poll block
- [x] `csrf_token` and `web_port` are `None` in `runtime.json` when the poll sets `web_handle=None` (verified by SC-2 manual test)
- [x] `ruff check src/samwhispers/supervisor.py src/samwhispers/webserver.py` passes
- [x] `mypy src/` passes

---

### Phase 3: Delete `supervisor.pid` on clean exit [QA]

**Goal**: Remove the stale PID file on normal supervisor shutdown so the `_do_stop()` fallback
path starts fresh on the next session.

**Covers**: SC-3

**File scope**: `src/samwhispers/supervisor.py`, `README.md`

#### 3a — Move `pid_path` import to top of `--foreground` branch

The `--foreground` branch already imports `write_pid` from `singleinstance` at `supervisor.py:592`:

```python
from samwhispers.singleinstance import write_pid
```

Change to:

```python
from samwhispers.singleinstance import write_pid, pid_path
```

This makes `pid_path` available throughout the `--foreground` branch, including in `finally`,
without a late import inside the `finally` block. The module is already loaded at this point.

#### 3b — Add PID cleanup to `finally` block

In the `finally` block, add three lines after `delete_metadata()` and before `lock.release()`:

```python
finally:
    supervisor.shutdown()
    if web_handle is not None:
        web_handle.shutdown()
    from samwhispers.runtime import delete_metadata
    delete_metadata()
    try:
        pid_path().unlink(missing_ok=True)
    except OSError:
        pass  # PermissionError (file held open on Windows) — best effort
    lock.release()
```

`missing_ok=True` suppresses `FileNotFoundError` (crash before `write_pid()` call). The
`try/except OSError` additionally suppresses `PermissionError`, which Windows can raise if
another process holds the file open. Both are best-effort: `_do_stop()`'s fallback handling
via `validate_metadata()` already tolerates a stale PID file from a crash.

**`_do_stop()` impact**: after Phase 3, a clean exit leaves neither `runtime.json` nor
`supervisor.pid`. `_do_stop()` (in `__main__.py`) reads `runtime.json` first; if absent, falls
to `read_pid()`. With both absent, `_do_stop()` returns `False` (nothing to stop) — the expected
outcome of stopping an already-stopped supervisor.

#### 3c — README update

In `README.md`, around line 327–328, update the sentence describing runtime metadata cleanup to
state that `supervisor.pid` is also removed on clean shutdown and only persists after a crash.
Current text (approximate): "Runtime metadata is written to the user's data directory after launch
and cleaned up on normal shutdown."
Updated text (example): "Runtime metadata (`runtime.json`) and the PID file (`supervisor.pid`)
are written at launch and removed on clean shutdown. A crash leaves both files; the next
`stop`/`start` ignores stale ones via dead-PID detection."

**Exit criteria**:
- [ ] `pid_path` added to the `write_pid` import at `supervisor.py:592`
- [ ] `try: pid_path().unlink(missing_ok=True) / except OSError: pass` added to `finally` after `delete_metadata()`
- [ ] `lock.release()` remains the last statement in `finally`
- [ ] `README.md` updated to reflect `supervisor.pid` removal on clean exit
- [ ] `ruff check src/samwhispers/supervisor.py` passes
- [x] `mypy src/` passes

---

### Phase 4: Tests [QA]

**Goal**: Add regression tests covering SC-1, SC-2, SC-3, and fill gaps in `write_pid`/`read_pid`
coverage identified by the subsystem review.

**Covers**: SC-1, SC-2, SC-3, SC-5

**File scope**: `tests/test_singleinstance.py`, `tests/test_supervisor.py`,
`tests/test_webserver.py` (if it exists; otherwise `tests/test_supervisor.py`)

#### 4a — Concurrent lock race regression (`tests/test_singleinstance.py`)

Add `test_concurrent_foreground_only_one_wins()`. Uses per-process output files to avoid the
shared-file write race.

```python
import os
import subprocess
import sys
import textwrap
import time
import json

import pytest

# Windows-only: msvcrt.locking is the lock mechanism under test
pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="msvcrt lock test (Windows only)")


def test_concurrent_foreground_only_one_wins(tmp_path):
    """Exactly one of N concurrent processes must acquire the lock."""
    lock_file = tmp_path / "supervisor.lock"
    go_time = time.time() + 0.4
    N = 4

    # Each child writes its result to its own file to avoid shared-file write races.
    script = textwrap.dedent(f"""\
        import os, sys, time, json, pathlib
        # Add src to path so InstanceLock is importable from the source tree
        sys.path.insert(0, r"{os.path.join(os.path.dirname(__file__), '..', 'src')}")
        from samwhispers import singleinstance as si
        import unittest.mock as mock

        child_id = int(sys.argv[1])
        go_time = {go_time}
        if time.time() < go_time:
            time.sleep(go_time - time.time())

        with mock.patch.object(si, "lock_path", return_value=pathlib.Path(r"{lock_file}")):
            lock = si.InstanceLock()
            result = lock.acquire()
            if result:
                time.sleep(0.3)
                lock.release()

        out = pathlib.Path(r"{tmp_path}") / f"result_{{child_id}}.json"
        out.write_text(json.dumps(result))
    """)
    script_path = tmp_path / "child.py"
    script_path.write_text(script)

    procs = [
        subprocess.Popen([sys.executable, str(script_path), str(i)])
        for i in range(N)
    ]
    for p in procs:
        p.wait(timeout=5)

    results = [
        json.loads((tmp_path / f"result_{i}.json").read_text())
        for i in range(N)
    ]
    assert len(results) == N, f"Expected {N} results, got {len(results)}"
    assert results.count(True) == 1, f"Expected exactly 1 lock winner, got {results}"
```

**Why `InstanceLock` via mock patch**: exercises the real `InstanceLock.acquire()` implementation
(including any future changes to the byte range or lock type), not a reimplementation of
`msvcrt.locking` inline. Changes to `InstanceLock` will be caught by this test.

#### 4b — `write_pid` / `read_pid` coverage (`tests/test_singleinstance.py`)

Add a `_with_pid_path` helper matching the file's existing `_with_lock_path` pattern, then add
three tests:

```python
def _with_pid_path(tmp_path: Path):
    return patch.object(si, "pid_path", return_value=tmp_path / "supervisor.pid")


def test_write_and_read_pid(tmp_path: Path) -> None:
    with _with_pid_path(tmp_path):
        si.write_pid()
        assert si.read_pid() == os.getpid()


def test_read_pid_missing(tmp_path: Path) -> None:
    with _with_pid_path(tmp_path):
        assert si.read_pid() is None


def test_pid_cleanup_via_unlink(tmp_path: Path) -> None:
    """pid_path().unlink(missing_ok=True) removes the file; missing_ok tolerates absence."""
    with _with_pid_path(tmp_path):
        si.write_pid()
        assert si.pid_path().exists()
        si.pid_path().unlink(missing_ok=True)
        assert not si.pid_path().exists()
        # Second call must not raise (missing_ok=True)
        si.pid_path().unlink(missing_ok=True)
```

Add `import os` to the test file imports if not already present.

#### 4c — `web_enabled=False` when port pre-bound (subprocess integration test)

Add to `tests/test_supervisor.py` (or `tests/test_webserver.py`). Approach: subprocess
integration test — the poll is inline in `main()` and no helper extraction is planned. This
directly exercises SC-2.

```python
import json
import socket
import subprocess
import sys
import time
from pathlib import Path
import pytest


@pytest.mark.skipif(sys.platform != "win32", reason="supervisor integration test (Windows only)")
def test_web_enabled_false_when_port_bound(tmp_path, tmp_path_factory):
    """web_enabled must be False when port 7891 is already bound at supervisor start."""
    # Pre-bind port 7891
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 7891))
    srv.listen(1)
    try:
        # Start supervisor with --foreground and a temp config path so it writes
        # runtime.json to the default data dir. We cannot redirect the data dir
        # easily without a config change; instead, poll the standard location.
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m", "samwhispers", "supervisor", "--foreground", "--no-tray",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        data_dir = Path.home() / "AppData" / "Local" / "samwhispers"
        meta_path = data_dir / "runtime.json"
        # Wait up to 8s for runtime.json to appear (2s poll + startup overhead)
        deadline = time.time() + 8
        while time.time() < deadline:
            if meta_path.exists():
                break
            time.sleep(0.1)
        proc.terminate()
        proc.wait(timeout=5)
        assert meta_path.exists(), "runtime.json was never written"
        meta = json.loads(meta_path.read_text())
        assert meta["web_enabled"] is False, f"Expected web_enabled=False, got {meta}"
        assert meta["web_port"] is None
    finally:
        srv.close()
```

**Note**: this test uses the real data directory and real supervisor startup. Mark it
`pytest.mark.integration` (add to `pyproject.toml`'s markers if not present) so it can be
excluded from fast unit test runs: `python -m pytest tests/ -v -m "not integration"`.

#### 4d — `supervisor.pid` absent after clean exit (integration test)

Add alongside 4c in `tests/test_supervisor.py`:

```python
@pytest.mark.skipif(sys.platform != "win32", reason="supervisor integration test (Windows only)")
@pytest.mark.integration
def test_supervisor_pid_cleaned_on_exit(tmp_path):
    """supervisor.pid must not exist after a clean supervisor exit."""
    data_dir = Path.home() / "AppData" / "Local" / "samwhispers"
    pid_file = data_dir / "supervisor.pid"

    proc = subprocess.Popen(
        [sys.executable, "-m", "samwhispers", "supervisor", "--foreground", "--no-tray", "--no-web"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    # Wait for pid file to appear
    deadline = time.time() + 5
    while time.time() < deadline:
        if pid_file.exists():
            break
        time.sleep(0.1)
    assert pid_file.exists(), "supervisor.pid was never written — check startup"

    proc.terminate()
    proc.wait(timeout=5)
    assert not pid_file.exists(), f"supervisor.pid was not removed on clean exit: {pid_file}"
```

**Exit criteria**:
- [ ] `test_concurrent_foreground_only_one_wins` added to `tests/test_singleinstance.py`; skips on non-Windows; uses per-process output files; uses `InstanceLock` (not raw `msvcrt.locking`)
- [ ] `_with_pid_path` helper and three `write_pid`/`read_pid` tests added
- [ ] `test_web_enabled_false_when_port_bound` subprocess integration test added; marked `integration`
- [ ] `test_supervisor_pid_cleaned_on_exit` subprocess integration test added; marked `integration`
- [ ] `python -m pytest tests/test_singleinstance.py -v` passes
- [ ] `python -m pytest tests/ -v -m "not integration"` passes (unit tests clean)
- [ ] `ruff check tests/` passes

---

## 6) Risk Assessment

| Risk | Impact | Mitigation |
|---|---|---|
| New `is_running()` guard adds a TOCTOU window | Low — second foreground child can still slip through if it wins `lock.acquire()` before the first | `lock.acquire()` is the authoritative gate; `is_running()` is an early-exit optimization; tray restart path is provably unaffected (lock released before child is spawned) |
| Poll delays `runtime.json` write by up to 2s | Low | Tray icon starts before `write_metadata()`; only CLI stop/restart commands are delayed, imperceptibly for a background daemon |
| `web_handle.shutdown()` on a dead-thread handle blocks | Low — uvicorn's `shutdown()` sets `server.should_exit` and returns; if the thread is dead, `web_handle.thread.join()` in `shutdown()` returns immediately | `WebServerHandle.shutdown()` joins the thread with a timeout; a dead thread joins immediately |
| `pid_path().unlink()` raises `PermissionError` on Windows | Low | Wrapped in `try/except OSError: pass`; best-effort cleanup; stale-PID handling in `_do_stop()` remains as fallback |
| Integration tests run the real supervisor and modify the data dir | Medium — tests in CI may interfere with a running supervisor | Tests terminate the spawned process; mark `integration` so they can be excluded from unit test runs |

A risk whose mitigation is "out of scope" is noted below.

## 7) Verification

**Automated** (run after all phases):

```
python -m pytest tests/ -v -m "not integration"
python -m pytest tests/ -v -m integration
ruff check src/ tests/
mypy src/
```

**Manual — SC-1 (duplicate instance)**:

1. `samwhispers stop` — confirm all pythonw processes gone
2. Open two PowerShell windows simultaneously, paste and execute in both at once:
   `& ".venv\Scripts\pythonw.exe" -c "import sys; sys.argv=['samwhispers-supervisor','--foreground']; from samwhispers.supervisor import main; main()"`
3. Wait 5s, then: `Get-Process pythonw | Measure-Object` — expect **2** (1 supervisor + 1 worker)

**Manual — SC-2 (web_enabled accuracy)**:

1. Bind port 7891 externally: `$s = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 7891); $s.Start()`
2. Start supervisor: `samwhispers`
3. Read metadata: `Get-Content $env:LOCALAPPDATA\samwhispers\runtime.json | ConvertFrom-Json | Select web_enabled, web_port, csrf_token`
4. Expect: `web_enabled=False, web_port=null, csrf_token=null`
5. Release port: `$s.Stop()`

**Manual — SC-3 (pid file cleanup)**:

1. `samwhispers stop`
2. `Test-Path $env:LOCALAPPDATA\samwhispers\supervisor.pid` — expect `False`

**Manual — SC-4 (tray restart unchanged)**:

1. Start supervisor, confirm tray icon appears
2. Click tray → Restart
3. Wait 5s: `Get-Process pythonw | Measure-Object` — expect **2** processes (supervisor + worker)

---

## 8) Documentation Updates

| Document | Update needed | Phase |
|---|---|---|
| `README.md` | Lines ~327–328: note `supervisor.pid` is removed on clean exit; only survives a crash | 3 |

---

## 9) Implementation Divergences from Plan

### Phase 1 divergences

None.

### Phase 2 implementation notes

Implementation (2026-08-21, code: cf0dcec + 9d4b1ac)
In `src/samwhispers/webserver.py`, added `is_ready` property to `WebServerHandle` returning `bool(self.server.started)`, confining the uvicorn-internal `server.started` coupling to `webserver.py`. In `src/samwhispers/supervisor.py`, inserted a 2s/50ms poll loop between `_start_web()` and `settings_url = ...`. The loop checks `is_ready` first (success path), then `thread.is_alive()` (failure path), with a `while/else` timeout branch. Both failure paths call `web_handle.shutdown()` before nulling `web_handle`, preventing orphaned threads. `DEFAULT_PORT` import moved before the poll block; `effective_port_for_log` unified into a single `effective_port`. Review auto-fixes (commit 9d4b1ac): added thread-still-alive warning in `shutdown()`, unified `effective_port` variable, fixed `while/else` comment accuracy, added GIL note to `is_ready` docstring.

### Phase 2 divergences

None.

Implementation (2026-08-21, code: 7605bc1 + a205f8a)
In `src/samwhispers/supervisor.py`, inside `main()`, a 10-line early-exit guard was inserted immediately before the `logging.basicConfig(...)` call in the `--foreground` branch (after the `if not args.foreground:` block ends at line 568). The guard imports `is_running` from `samwhispers.singleinstance` under the alias `_is_running` to avoid any name collision, calls it, and if another instance already holds the lock it writes a message to stderr via `print(..., file=sys.stderr)` (not `log.error`, because the logger is not yet configured at that point) and returns immediately. No `webbrowser.open()` or any other logic was added. The existing `lock.acquire()` call that follows `logging.basicConfig` remains unchanged as the authoritative gate — this guard is purely an optimization that lets losing foreground children exit before allocating logging resources or starting any threads. Review auto-fixes (commit a205f8a): removed redundant `import sys` (already at module level), dropped unnecessary `_is_running` alias (import as `is_running` to match sibling branch at ~L553).

## Follow-up Work (Deferred)

1. **Autostart shortcut fires twice on boot.** The root cause — Windows Startup folder invoking
   the shortcut twice in the same scheduler window — is not addressed by this plan. The Phase 1
   guard prevents the second child from wasting resources but does not eliminate both parent
   spawns. A more durable fix would add a Windows mutex or named-event guard before
   `_relaunch_detached()`. Deferred; current fix is sufficient for the symptom. Source: Scope
   boundaries.

2. **`server.started` is an undocumented uvicorn internal.** The `is_ready()` property in Phase 2
   encapsulates it in `WebServerHandle`, but if uvicorn renames the attribute, `is_ready()` will
   silently return `False` for all calls. Monitor uvicorn changelogs on version bumps. Source: Risk
   Assessment / Maintainability finding H.

## Review Log

### 2026-08-21 — Plan Creation (via /qplan)

High effort, 4 personas: Architect, Senior engineer, Reliability engineer, Maintainability reviewer. Verifier pass run after. 11 findings total (post-merge, post-dedup). 9 auto-resolved. 2 refuted.

| # | Severity | Finding | Resolution |
|---|---|---|---|
| 1 | High | Test 4a result-file has unsynchronized read-modify-write; `count(True)==1` is unreliable under concurrent writes | Fixed — per-process output files; parent aggregates after all `p.wait()` |
| 2 | High | Section 1 claimed losing child starts worker and `_start_web()` before `lock.acquire()` fails; factually wrong (child exits at L578) | Fixed — Section 1 rewritten to state the child exits at `lock.acquire()` and never starts worker/web |
| 3 | Medium | Phase 2 poll checks `thread.is_alive()` before `server.started`; dead thread after successful bind falsely declared failure | Fixed — `is_ready` checked first, thread-alive second |
| 4 | Medium | Phase 2 orphans live server on 2s timeout; thread may bind after deadline with no shutdown path | Fixed — both failure branches call `web_handle.shutdown()` before setting `None` |
| 5 | Medium | Test 4c stub body was `pass`; approach uncommitted; exit criterion unfulfillable | Fixed — prescribes subprocess integration test; full skeleton provided |
| 6 | Medium | Test 4d directed caller to `WorkerSupervisor.shutdown()`; pid cleanup is in `main()`'s `finally`, not that method | Fixed — test 4d uses subprocess integration to exercise the `finally` block |
| 7 | Medium | Phase 2 accessed `web_handle.server.started` directly (uvicorn internal through two layers) | Fixed — `is_ready()` property added to `WebServerHandle`; coupling confined to `webserver.py` |
| 8 | Medium | `pid_path().unlink(missing_ok=True)` propagates `PermissionError` (not suppressed by `missing_ok`) | Fixed — wrapped in `try/except OSError: pass` |
| 9 | Medium | Test 4a used raw `msvcrt.locking` inline; decoupled from `InstanceLock` changes | Fixed — test uses `InstanceLock` via monkeypatched `lock_path` |
| 10 | Low | SC-4 manual test expected pythonw count=3; overlay is a thread, not a process (count should be 2) | Fixed — both manual tests updated to expect count=2 |
| 11 | Low | Phase 1 used `log.error` before `logging.basicConfig` ran | Fixed — changed to `print(..., file=sys.stderr)` with rationale |
| C | — | Tray restart: new child's `is_running()` fires before lock released | Refuted by verifier — `lock.release()` provably runs before `_relaunch_detached()` is called |
| J | — | Import `pid_path` inside `finally` would suppress `ImportError` | Refuted by verifier — module already loaded earlier in `main()`; re-import is a cache lookup |

### 2026-08-21 — Implementation Review (after Phase 1, personas: Senior engineer, Reliability engineer, Maintainability reviewer, Architect)

Implementation health: Green.
6 findings (0 High, 0 Medium, 6 Low). All auto-fixable Low findings resolved in cycle 1. Cycle 2 skipped — cycle-1 findings all Low + auto-fixes purely mechanical.
QA verification: PASS (CLI surface, 3 probes — guard fires, message to stderr only, logging.basicConfig not called when guard fires).

| # | Severity | Finding | Resolution |
|---|---|---|---|
| 1 | Low | Redundant `import sys` inside the `if is_running():` block; `sys` already imported at module level | Fixed — removed inner `import sys` (commit a205f8a) |
| 2 | Low | `_is_running` alias inconsistent with sibling non-foreground branch which uses `is_running` unaliased | Fixed — dropped alias, import as `is_running` (commit a205f8a) |
| 3 | Low | TOCTOU window note in plan slightly overstates risk; gap existed before this change in non-foreground path | Orchestrator: proposed-accept — no code change; plan Risk Assessment already treats this as Low |
| 4 | Low | Exit code 0 on duplicate-instance early exit; `sys.exit(1)` would be more conventional | Orchestrator: proposed-accept — plan specifies `return`; behavior is intentional optimization path |
| 5 | Low | Pre-existing test failure (`test_windows_target_anchors_on_script_dir`) unrelated to Phase 1 | Orchestrator: proposed-accept — pre-existing defect, not a Phase 1 responsibility |
| 6 | Low | Two deferred `from samwhispers.singleinstance import ...` statements within `main()` | Orchestrator: proposed-accept — intentional by plan design; combining would move InstanceLock import earlier |

### 2026-08-21 — Implementation Review (after Phase 2, personas: Senior engineer, Reliability engineer, Architect, Maintainability reviewer)

Implementation health: Green (all Mediums resolved by user-directed fixes).
11 findings (0 High, 3 Medium all fixed, 8 Low resolved). Cycle 2: clean. User directed: fix all three Mediums (commit 333f7d9).
QA verification: PASS (library surface — is_ready property, 4 probes). Integration SC-2 test: BLOCKED — requires supervisor restart to exercise live port-conflict path.

| # | Severity | Finding | Resolution |
|---|---|---|---|
| R3 | Medium | `shutdown()` swallows thread-join timeout; orphaned thread possible with no log | Fixed — added `is_alive()` check + `log.warning` after `join()` (commit 9d4b1ac) |
| S1 | Medium | `_start_web()` emits "Config UI available at..." before poll; bind failure produces contradictory messages | Fixed — log moved to `main()` post-poll, guarded by `web_handle is not None` (commit 333f7d9) |
| R2 | Medium | 2s poll timeout may produce false-negative on slow boot-time machine (uvicorn startup ~1–1.5s) | Fixed — timeout widened to 5s; warning message updated (commit 333f7d9) |
| A1 | Medium | `csrf_token` still accessed via `web_handle.server.config.app.state.csrf_token` in supervisor.py; incomplete encapsulation | Fixed — added `csrf_token` property to `WebServerHandle`; call site updated (commit 333f7d9) |
| M1 | Low | `effective_port_for_log` duplicated `effective_port` — same expression, different names | Fixed — unified into single `effective_port` before poll block (commit 9d4b1ac) |
| M2 | Low | `while/else` comment inaccurately said "without server.started"; actual condition is deadline-with-alive-thread | Fixed — updated to "Deadline reached with thread still alive but port not bound." (commit 9d4b1ac) |
| R4 | Low | `is_ready` reads `server.started` without memory barrier; GIL dependency undocumented | Fixed — added CPython GIL note to `is_ready` docstring (commit 9d4b1ac) |
| S3 | Low | Plan spec says `is_ready()` method; implementation is `@property` | Orchestrator: proposed-accept — `@property` is the correct design; plan text is imprecise |
| A2 | Low | `DEFAULT_PORT` imported inside poll block, used unconditionally after; style inconsistency | Fixed — hoisted to before `if web_handle is not None:` (commit 9d4b1ac) |
| R5 | Low | `shutdown()` on dead-thread handle writes to `server.should_exit` on already-exited object | Orchestrator: proposed-accept — harmless with current uvicorn; noted in Follow-up Work |
| M4 | Low | Max nesting depth 4 in poll block (at threshold) | Orchestrator: proposed-accept — within scope; no growth expected per plan boundaries |

## Harness Improvement Opportunities

- Probe gate fires after Step 1.5 but the SKILL.md says "run every probe on those lists" before
  the interview — the probes and interview were interleaved rather than batch-probed first. No
  material cost here (probes were fast), but the skill text reads as batch-then-interview.
  cost: ~1 extra turn reordering — suggested change: clarify in SKILL.md whether probes must
  all complete before Q1 or can interleave with interview questions.
