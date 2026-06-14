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

## Pattern

### Worker state lifecycle: STOPPED -> STARTING -> RUNNING -> PAUSED

**Why**: The supervisor has 4 worker states. STARTING transitions to RUNNING after 3 consecutive healthy poll ticks (~3s). Tray icon colors: grey=STOPPED, blue=STARTING, green=RUNNING, amber=PAUSED.
**How to apply**: When adding supervisor features that depend on worker readiness, gate on RUNNING state (not STARTING). When modifying _set_state or the monitor loop, maintain the 3-tick transition invariant.
**Source**: Plan 260613-1911_PRODUCTION_STABILIZATION_ERROR_VISIBILITY.md Phase 3 | **Verified**: 2026-06-14

### SamWhispers uses direct implementation without /qplan for trivial changes

**Why**: The user frequently asks for direct implementation of features (overlay polish, model management UI, config webUI rework) without going through /qexplore -> /qplan. Only multi-concern production-grade work gets the full lifecycle treatment.
**How to apply**: For SamWhispers tasks that are single-file or single-concern (UI rework, visual polish, feature addition to existing modules), implement directly. Reserve /qexplore->/qplan for cross-cutting concerns or production-critical changes with failure modes.
**Source**: Sessions 45ad4165, ce4f96dc, 44f3f23c (direct) vs 60a930c7, 8d312e75 (full lifecycle) | **Verified**: 2026-06-14


### Deferred Timer(0) for audio callback stop actions — never call lock-acquiring methods from _callback

**Why**: The audio callback thread holds `_lock`. Any method that also acquires `_lock` (like `stop()`) will deadlock if called directly from `_callback`. Use `threading.Timer(0, method).start()` to defer to a new thread.
**How to apply**: When adding behavior in `AudioRecorder._callback` that triggers stop/state-change, defer via Timer(0). Include a boolean flag (e.g., `_vad_fired`) to prevent double-fire, and reset it in `start()`.
**Source**: Plan 260614_SNIPPETS_AND_VAD, Phase 2 + Post-Implementation Review finding #1 | **Verified**: 2026-06-14
