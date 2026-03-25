# Code Review 2026-03-25 -- Fix Plan

> **Date**: 2026-03-25
> **Status**: In progress (Phase 1 complete)
> **Scope**: Address all actionable findings from the 2026-03-25 code review (P0 through P3), excluding P2 #6 (whisper-server subprocess management) which has its own plan.
> **Estimated effort**: 1-2 days

---

## 1) Goal

Fix 11 code review findings spanning a critical bug (hotkey listener dies permanently on inject failure), a resource leak, a latent PowerShell injection vulnerability, several platform correctness issues, and minor cleanup items. Finding #9 (config.toml in .gitignore) is already addressed and excluded.

## 2) Current State

| # | Finding | File | Lines | Verified |
|---|---|---|---|---|
| 1 | Hotkey dies on inject failure | `app.py` | 201-203 | Yes -- no try/finally around inject |
| 2 | WSLHotkeyListener.stop() leaks on timeout | `hotkeys.py` | 348-355 | Yes -- no TimeoutExpired handling |
| 3 | PowerShell injection in notifications | `notify.py` | 57-64 | Yes -- f-string interpolation with only single-quote escaping |
| 4 | Config reads without encoding | `config.py` | 264, 268 | Yes -- `read_text()` with no encoding arg |
| 5 | shutil.which() wrong for absolute WSL paths | `wsl.py` | 30-32 | Yes -- checks executable bit, unreliable for .exe in WSL |
| 7 | Startup checks non-fatal | `app.py` | 207-270 | Yes -- whisper/clipboard checks are warnings only |
| 8 | time.sleep() blocks shutdown in retry | `transcribe.py` | 60 | Yes -- sleep not interruptible by shutdown event |
| 10 | assert isinstance stripped in -O | `inject.py` | 42 | Yes -- `assert isinstance(self._keyboard, Controller)` |
| 11 | close() reads _recording without lock | `audio.py` | 130 | Yes -- `if self._recording:` outside lock |
| 12 | Duration estimate hardcodes WAV constants | `app.py` | 181 | Yes -- magic 44 and 2 duplicated from audio.py |

## 3) Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Notification data passing | Environment variables (`$env:SW_TITLE`, `$env:SW_MSG`) | Eliminates all injection vectors without complex escaping |
| WSL absolute path check | `os.path.isfile()` instead of `shutil.which()` | `.exe` files in WSL often lack executable bit; existence check is sufficient for known absolute paths |
| Fatal startup checks scope | Whisper server only (non-managed case) | Clipboard and notification failures are recoverable; unreachable whisper server means zero functionality |
| Retry sleep interruptibility | `threading.Event.wait(timeout)` replacing `time.sleep()` | Allows clean shutdown without changing retry semantics |
| WAV constant duplication | Reuse `min_wav_size()` for the threshold; inline the duration formula with a comment | Full extraction is over-engineering for a logging line |

## 4) External Dependencies & Costs

None. All changes are code-only, no infrastructure, CI/CD, or third-party service changes.

## 5) Implementation Phases

### Phase 1: P0 -- Critical bug fixes

**Goal**: Fix the two bugs that can leave the app in a broken state.

#### 1a. Hotkey listener survives inject failure

`src/samwhispers/app.py:201-203`

```python
# Before:
self.hotkey_listener.suppress()
self.injector.inject(text)
self.hotkey_listener.resume()

# After:
self.hotkey_listener.suppress()
try:
    self.injector.inject(text)
finally:
    self.hotkey_listener.resume()
```

#### 1b. WSLHotkeyListener.stop() handles timeout

`src/samwhispers/hotkeys.py:348-355`

```python
# Before:
def stop(self) -> None:
    self._running = False
    if self._process:
        self._process.terminate()
        self._process.wait(timeout=5)
        self._process = None

# After:
def stop(self) -> None:
    self._running = False
    if self._process:
        self._process.terminate()
        try:
            self._process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait(timeout=10)
        self._process = None
```

Add `import subprocess` to the top-level imports in `hotkeys.py` (currently only imported locally inside `start()`).

**Tests**: Add a test for the timeout escalation path and a test for the inject try/finally recovery:

```python
# test_wsl.py
def test_wsl_hotkey_listener_stop_kills_on_timeout(monkeypatch):
    """stop() escalates to kill() when terminate() + wait() times out."""
    mock_proc = MagicMock()
    mock_proc.wait.side_effect = [subprocess.TimeoutExpired("cmd", 5), None]

    listener = WSLHotkeyListener("ctrl+space", "hold", MagicMock(), MagicMock())
    listener._process = mock_proc
    listener.stop()

    mock_proc.terminate.assert_called_once()
    mock_proc.kill.assert_called_once()
    assert listener._process is None
```

```python
# test_app.py
def test_inject_failure_resumes_hotkey_listener():
    """Hotkey listener is resumed even when inject() raises."""
    app = _make_app()
    app.injector.inject.side_effect = RuntimeError("clipboard crash")
    app._state = State.PROCESSING

    app._process_recording(sample_wav)  # should not raise (caught by _process_loop)

    app.hotkey_listener.resume.assert_called_once()
```

**Exit criteria**:
- [x] `pytest tests/test_wsl.py tests/test_integration.py -v` passes
- [ ] Manual: inject failure (e.g., kill clipboard backend) does not kill hotkey listener

> **Completed 2026-03-25.** Implemented as planned. No divergences.

---

### Phase 2: P1 -- Security and platform correctness

**Goal**: Close the latent PowerShell injection vector and fix platform-specific correctness issues.

#### 2a. Notification data via environment variables

`src/samwhispers/notify.py:57-64`

```python
# Before:
t = title.replace("'", "''")
m = message.replace("'", "''")
script = (
    "Add-Type -AssemblyName System.Windows.Forms;"
    "$n = New-Object System.Windows.Forms.NotifyIcon;"
    "$n.Icon = [System.Drawing.SystemIcons]::Information;"
    "$n.Visible = $true;"
    f"$n.ShowBalloonTip(3000, '{t}', '{m}', 'Info');"
    "Start-Sleep -Milliseconds 3100;"
    "$n.Dispose()"
)
subprocess.Popen(
    [ps, "-NoProfile", "-WindowStyle", "Hidden", "-c", script],
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)

# After:
script = (
    "Add-Type -AssemblyName System.Windows.Forms;"
    "$n = New-Object System.Windows.Forms.NotifyIcon;"
    "$n.Icon = [System.Drawing.SystemIcons]::Information;"
    "$n.Visible = $true;"
    "$n.ShowBalloonTip(3000, $env:SW_TITLE, $env:SW_MSG, 'Info');"
    "Start-Sleep -Milliseconds 3100;"
    "$n.Dispose()"
)
subprocess.Popen(
    [ps, "-NoProfile", "-WindowStyle", "Hidden", "-c", script],
    env={**os.environ, "SW_TITLE": title, "SW_MSG": message},
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)
```

Add `import os` at the top of `notify.py`.

#### 2b. Config reads with explicit UTF-8 encoding

`src/samwhispers/config.py:264, 268`

```python
# Before:
raw = tomllib.loads(p.read_text())
# ...
raw = tomllib.loads(found.read_text())

# After:
raw = tomllib.loads(p.read_text(encoding="utf-8"))
# ...
raw = tomllib.loads(found.read_text(encoding="utf-8"))
```

#### 2c. WSL fallback uses os.path.isfile()

`src/samwhispers/wsl.py:28-32`

```python
# Before:
for prefix in ["/mnt/c/Windows/System32", "/mnt/c/Windows/System32/WindowsPowerShell/v1.0"]:
    candidate = f"{prefix}/{name}"
    if shutil.which(candidate):
        return candidate

# After:
for prefix in ["/mnt/c/Windows/System32", "/mnt/c/Windows/System32/WindowsPowerShell/v1.0"]:
    candidate = f"{prefix}/{name}"
    if os.path.isfile(candidate):
        return candidate
```

Add `import os` at the top of `wsl.py`.

**Tests**: Update `test_notify.py::test_notify_windows_calls_powershell` to assert `env` kwarg contains `SW_TITLE`/`SW_MSG`. Update `test_wsl.py` if any test mocks `shutil.which` for the fallback path.

**Exit criteria**:
- [ ] `pytest tests/test_notify.py tests/test_config.py tests/test_wsl.py -v` passes
- [ ] Notification still works on WSL (manual check)

---

### Phase 3: P2 -- Robustness improvements

**Goal**: Make startup failures visible and allow clean shutdown during retries.

#### 3a. Fatal whisper server check (non-managed case)

`src/samwhispers/app.py` -- `_startup_checks()`

```python
# Before:
elif self.whisper.health_check():
    log.info("Whisper server: OK")
else:
    log.warning(
        "Whisper server at %s is not reachable. "
        "Transcription will fail until it's started.",
        self.config.whisper.server_url,
    )

# After:
elif self.whisper.health_check():
    log.info("Whisper server: OK")
else:
    log.error(
        "Whisper server at %s is not reachable. "
        "Start the server and try again.",
        self.config.whisper.server_url,
    )
    raise SystemExit(1)
```

#### 3b. Interruptible retry sleep

`src/samwhispers/transcribe.py` -- `_post_with_retry()`

Add a `shutdown_event` parameter to `WhisperClient.__init__()` (optional, defaults to `None`). Use `event.wait(timeout)` instead of `time.sleep()` in the retry loop. There are two `time.sleep(backoff)` calls in `_post_with_retry` (lines ~60 and ~70) -- both must be replaced:

```python
# In __init__:
def __init__(self, server_url: str, language: str = "auto",
             shutdown_event: threading.Event | None = None) -> None:
    ...
    self._shutdown_event = shutdown_event

# In _post_with_retry, replace BOTH time.sleep(backoff) calls:
# Before:
time.sleep(backoff)

# After:
if self._shutdown_event is not None:
    if self._shutdown_event.wait(backoff):
        raise RuntimeError("Shutdown requested during retry")
else:
    time.sleep(backoff)
```

Note: `event.wait(backoff)` blocks for `backoff` seconds (acting as the sleep) and returns `True` immediately if the event is set. This replaces `time.sleep` without doubling the wait.

Wire the shutdown event from `SamWhispers.__init__()` in `app.py`:

```python
self.whisper = WhisperClient(
    server_url=self.config.whisper.server_url,
    language=self._languages[0],
    shutdown_event=self._shutdown_event,
)
```

**Tests**: Update `test_app.py::_make_app` to verify the event is passed. Add a test in `test_transcribe.py` that the retry exits early when the event is set.

**Exit criteria**:
- [ ] `pytest tests/test_app.py tests/test_transcribe.py -v` passes
- [ ] App exits cleanly within ~1s even if a retry sleep is in progress

---

### Phase 4: P3 -- Minor cleanup

**Goal**: Fix minor correctness and style issues.

#### 4a. Replace assert with proper guard

`src/samwhispers/inject.py:42`

```python
# Before:
assert isinstance(self._keyboard, Controller)

# After:
if self._keyboard is None:
    raise RuntimeError("Keyboard controller not initialized")
```

#### 4b. Lock in AudioRecorder.close()

`src/samwhispers/audio.py:130`

```python
# Before:
def close(self) -> None:
    if self._recording:
        self.stop()

# After:
def close(self) -> None:
    with self._lock:
        recording = self._recording
    if recording:
        self.stop()
```

#### 4c. Document WAV magic numbers

`src/samwhispers/app.py:181`

```python
# Before:
duration = (len(wav_bytes) - 44) / (self.config.audio.sample_rate * 2)

# After:
# WAV header = 44 bytes, 16-bit mono PCM = 2 bytes/sample (matches audio.py)
duration = (len(wav_bytes) - 44) / (self.config.audio.sample_rate * 2)
```

**Exit criteria**:
- [ ] `pytest -v` full suite passes
- [ ] No regressions

---

## 6) Risk Assessment

| Risk | Impact | Mitigation |
|---|---|---|
| Env var approach breaks notification on some PowerShell versions | Notifications silently fail (already fire-and-forget) | Existing `notify()` exception handler catches and logs |
| Fatal whisper check blocks devs who start app before server | App refuses to start | Clear error message tells them what to do; managed server path already works this way |
| shutdown_event threading adds complexity to WhisperClient | Subtle concurrency bugs | Event is optional; existing behavior preserved when None; tested explicitly |

## 7) Verification

```bash
# Full test suite
pytest -v

# Type checking (if configured)
mypy src/samwhispers/

# Manual smoke test on WSL
# 1. Start whisper-server
# 2. Run samwhispers
# 3. Push-to-talk, verify transcription + injection
# 4. Kill clipboard backend mid-recording, verify hotkey recovers
# 5. Check notification appears with special characters in language name
```

## 8) Documentation Updates

| Document | Update needed | Phase |
|---|---|---|
| `docs/code_review_260325.md` | Mark findings as resolved after implementation | Phase 4 |

## 9) Implementation Divergences from Plan

_Reserved -- filled during implementation._

---

## Review Log

### 2026-03-25 -- Sub-agent review (cycle 1)

Personas: Implementability reviewer, Security auditor, Reliability engineer.

14 unique findings after deduplication. 5 auto-resolved, 3 noted.

| # | Finding | Severity | Status |
|---|---|---|---|
| 1 | Phase 3b: `base_url=` should be `server_url=` (TypeError at runtime) | High | Resolved -- fixed parameter name in plan |
| 2 | Phase 1b: `subprocess` not imported at module level in `hotkeys.py` | High | Resolved -- added explicit import note in plan |
| 3 | Phase 3b: sleep logic double-waits when event provided but not set | High | Resolved -- restructured to use `event.wait()` as the sleep itself |
| 4 | Missing test for Phase 1a (inject try/finally recovery) | Medium | Resolved -- added test skeleton in Phase 1 |
| 5 | Phase 3b: two `time.sleep(backoff)` sites, plan only showed one | Medium | Resolved -- clarified both sites must be replaced |
| 6 | Phase 1b: test skeleton had incomplete constructor args | Low | Resolved -- fixed constructor args |
| 7 | Phase 1b: bare `wait()` after `kill()` could hang | Low | Resolved -- added `timeout=10` |
| 8 | Line numbers off by 1-5 throughout | Info | Noted -- minor drift from review to plan, not blocking |

### 2026-03-25 -- Implementation Review (after Phase 1, persona: Reliability engineer)

Implementation health: Green.
7 findings (0 High, 0 Medium, 2 Low, 5 Info).

| # | Persona | Finding | Severity | Confidence | Resolution |
|---|---|---|---|---|---|
| 1 | Reliability | Second `wait()` after `kill()` could still raise `TimeoutExpired` on unkillable process | Low | High | Noted -- OS-level anomaly, acceptable risk |
| 2 | Reliability | No logging on kill escalation path | Low | Medium | Noted -- minor observability improvement, not blocking |
