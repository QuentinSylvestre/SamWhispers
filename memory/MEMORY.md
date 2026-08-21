## Feedback

### `re.sub(pattern, replacement, text)` treats replacement as template — use lambda for literal

**Why**: `re.sub` interprets `\1`, `\U`, etc. in the replacement string. Real-world expansions (file paths, code) contain backslashes that crash or silently corrupt output.
**How to apply**: When using `re.sub` with user-provided replacement text, always use `pattern.sub(lambda m: replacement, text)` not `pattern.sub(replacement, text)`.
**Source**: Plan 260614_SNIPPETS_AND_VAD, Phase 1 review finding #1 | **Verified**: 2026-06-14

## Decision

### Python -m breaks pystray on Windows — use import-based launch

**Why**: Running supervisor via `python -m samwhispers.supervisor` uses runpy which creates a fresh __main__ namespace that breaks pystray's Shell_NotifyIcon message pump on Windows. Import-based launch works correctly.
**How to apply**: Never launch the supervisor via `python -m` on Windows. Use `-c "from samwhispers.supervisor import main; main()"` in _relaunch_detached. This applies to any future entry-point refactoring.
**Source**: Session 6e792e80 — multi-hour debugging, 20+ test iterations to isolate | **Verified**: 2026-06-14

### Notifications use existing notify.py (PowerShell balloon tips) — no plyer needed

**Why**: notify.py already has full Windows support via PowerShell balloon tips. No new notification dependency needed.
**How to apply**: Do not add notification libraries (plyer, win10toast, etc.). The existing notify.py handles Windows (PowerShell), Linux (notify-send), and WSL. Extend it directly for new notification needs.
**Source**: Session 60a930c7 — /qexplore review correction | **Verified**: 2026-06-14

### Exit code 78 (EX_CONFIG) for deterministic startup failures — no retry

**Why**: The supervisor previously retried all non-zero exits 5 times, even for deterministic startup failures (missing model, bad config). Exit code 78 signals 'configuration error, do not retry' and the monitor loop stops immediately.
**How to apply**: All startup-failure code paths in app.py (_startup_checks) must use SystemExit(78), not SystemExit(1). The supervisor treats code 78 as no-retry + user notification.
**Source**: Plan 260613-1911_PRODUCTION_STABILIZATION_ERROR_VISIBILITY.md Phase 1 | **Verified**: 2026-06-14

### Overlay uses PIL 4x supersampling for anti-aliased rendering

**Why**: Tkinter's create_arc/create_oval look pixelated. PIL rendering at 4x resolution with LANCZOS downsampling produces smooth anti-aliased overlays.
**How to apply**: All new overlay visual elements must use the PIL 4x supersample + LANCZOS downsample pattern established in _render_spinner and _render_checkmark. Do not use raw Tk drawing primitives for user-facing UI.
**Source**: Session 45ad4165 — overlay polishing | **Verified**: 2026-06-14

### Hotkey listener must accept injected key events — Logitech mouse mapping depends on it

**Why**: The _is_injected filter in hotkeys.py discarded all software-generated keypresses on Windows, which blocked the user's Logitech Options+ mouse-button-to-keyboard mapping from triggering recording. It was deliberately removed with user approval (commit c6c324b, 2026-07-14); paste self-trigger protection is handled by the existing suppress()/resume() mechanism alone. A future agent could plausibly re-add an injected-event filter as a safety improvement and silently break the user's primary trigger path.
**How to apply**: Do not re-introduce an injected-event check in hotkeys.py _on_press/_on_release. Rely on suppress()/resume() for paste self-trigger protection; if self-triggering regressions appear, fix within suppress()/resume(), not by filtering injected events. (Only the unused `injected` parameters remain at hotkeys.py:140,194 — verified 2026-07-16.)
**Source**: Session 14892e46 (2026-07-14) + commit c6c324b | **Verified**: 2026-07-16

## Pattern

### Worker state lifecycle: STOPPED -> STARTING -> RUNNING -> PAUSED

**Why**: The supervisor has 4 worker states. STARTING transitions to RUNNING after 3 consecutive healthy poll ticks (~3s). Tray icon colors: grey=STOPPED, blue=STARTING, green=RUNNING, amber=PAUSED.
**How to apply**: When adding supervisor features that depend on worker readiness, gate on RUNNING state (not STARTING). When modifying _set_state or the monitor loop, maintain the 3-tick transition invariant.
**Source**: Plan 260613-1911_PRODUCTION_STABILIZATION_ERROR_VISIBILITY.md Phase 3 | **Verified**: 2026-06-14

### SamWhispers uses direct implementation without /qplan for trivial changes

**Why**: The user frequently asks for direct implementation of features (overlay polish, model management UI, config webUI rework) without going through /qexplore -> /qplan. Only multi-concern production-grade work gets the full lifecycle treatment.
**How to apply**: For SamWhispers tasks that are single-file or single-concern (UI rework, visual polish, feature addition to existing modules), implement directly. Reserve /qexplore->/qplan for cross-cutting concerns or production-critical changes with failure modes.
**Source**: Sessions 45ad4165, ce4f96dc, 44f3f23c (direct) vs 60a930c7, 8d312e75 (full lifecycle) | **Verified**: 2026-07-16
**Governance-conflict**: contradicts shared/AGENTS.md § Workflow (Plan-before-act gate) — adjudicated 2026-07-16: keep-entry (user affirms the standing waiver for SamWhispers single-concern work)
**Governance-conflict-quote**: "Only tasks meeting the Trivial-tier criteria defined in `/qplan` Step 1 (unambiguous approach, ≤1 file, no irreversible changes, no external dependencies, no breaking changes) proceed without this prompt. If the user chooses direct implementation, proceed without further confirmation."


### Deferred Timer(0) for audio callback stop actions — never call lock-acquiring methods from _callback

**Why**: The audio callback thread holds `_lock`. Any method that also acquires `_lock` (like `stop()`) will deadlock if called directly from `_callback`. Use `threading.Timer(0, method).start()` to defer to a new thread.
**How to apply**: When adding behavior in `AudioRecorder._callback` that triggers stop/state-change, defer via Timer(0). Include a boolean flag (e.g., `_vad_fired`) to prevent double-fire, and reset it in `start()`.
**Source**: Plan 260614_SNIPPETS_AND_VAD, Phase 2 + Post-Implementation Review finding #1 | **Verified**: 2026-06-14


### pytest triggers KeyboardInterrupt under kiro-cli due to hotkey/pynput terminal I/O conflict

**Why**: Running pytest from kiro-cli repeatedly crashes the session because pynput's hotkey listener intercepts terminal control sequences during test output. Caused multiple session interruptions and user frustration.
**How to apply**: When running pytest for SamWhispers from kiro-cli, stop the running SamWhispers instance first, OR use `.venv/Scripts/python.exe -m pytest` and pipe output to a temp file to avoid terminal contention.
**Source**: Sessions 33e9f3b8, 3abed80b, 4f717cb4 - repeated KeyboardInterrupt during test execution | **Verified**: 2026-07-05

### Window flash on restart requires CREATE_NO_WINDOW flag on subprocess creation

**Why**: The detached relaunch subprocess briefly shows a console window on Windows. Required 5+ fix iterations across 2 sessions spanning 5 days before the correct flag combination resolved it.
**How to apply**: Detached relaunch on Windows uses the `pythonw.exe` launcher (`_python_launcher`), a `STARTUPINFO` with `STARTF_USESHOWWINDOW` and `wShowWindow = 0` (SW_HIDE), and `creationflags = _CREATE_NEW_PROCESS_GROUP | _CREATE_NO_WINDOW` — see `_relaunch_detached` in `src/samwhispers/supervisor.py`. **Do not add `DETACHED_PROCESS`**: the `_DETACHED_PROCESS` constant defined in that file has zero use sites and is dead. `autostart.py` is the one place that legitimately pairs `_DETACHED_PROCESS | _CREATE_NO_WINDOW`. Testing still requires a full kill + fresh start cycle.
**Source**: Sessions abcbfed7, afd1ec22 - 5+ fix iterations before resolution; procedure superseded by commit 04d6ecc (2026-06-19) "fix(windows): eliminate window flash and broken autostart on boot", corrected 2026-07-28 | **Verified**: 2026-07-28

### Duplicate supervisor instances cause tray icon loss and settings_url becoming None

**Why**: When two supervisor processes run simultaneously (e.g. autostart fires while a prior instance is still alive), neither has the web server listening. The `settings_url` field is `None` in the running supervisor because only one instance "wins" the socket, and the tray icon may show no functional menu. Diagnosis requires checking for multiple Python processes matching the supervisor pattern.
**How to apply**: When the tray icon appears unresponsive or the settings window cannot be opened, check for duplicate supervisor instances first: `Get-Process python | Where-Object { $_.CommandLine -like '*samwhispers*' }` (or `ps aux | grep samwhispers`). Kill all but one and verify the survivor has `settings_url` set. The root race is `_relaunch_detached()` being called while the prior process is still cleaning up — the autostart path is the most common trigger.
**Source**: Session 48178640 (2026-08-05) — "There are **two supervisor instances running** (PIDs 9296 and 26884)... neither has the web server listening... The web server (`settings_url`) is `None` in the running supervisor." | **Verified**: 2026-08-18 (sweep, verifier-confirmed)

## Declined

<!-- Declination records: the user's Skip of an agent-initiated memory proposal. A live row suppresses re-proposal of that subject for 60 days. Sessions append rows only; the /qdream sweep prunes expired rows. Row format: - "<proposed heading>" — declined <YYYY-MM-DD> (<reason, if given>) -->

- "Confirm daemon thread bind before writing metadata — poll is_ready(), not thread.is_alive() alone" — declined 2026-08-21
- "Integration tests for supervisor launch must use direct module invocation, not subcommand path, and must account for venv launcher PID indirection" — declined 2026-08-21
- "CTRL_BREAK_EVENT only delivers to processes sharing the same console — use unit test with mock-patched paths for finally-block coverage" — declined 2026-08-21