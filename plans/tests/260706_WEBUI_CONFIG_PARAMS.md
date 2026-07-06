# Test Plan: Web UI — Configurable Parameters

approach: organic
created: 2026-07-06T09:50:00Z
last_executed: 2026-07-06T10:05:00Z  # 4 findings (0H/3M/1L), 12/14 features fully covered, 2 partially-verified
resource_constraints:
  - All features share a single browser session (config state is shared)
  - whisper-server restart takes ~3-5s (model reload); worker restart is <1s
  - Model downloads require internet; other features are local-only

## Target

The SamWhispers config web UI at `http://127.0.0.1:7891/` — specifically how well configurable parameters load, validate, save, and take effect.

## Resources

- Web UI: http://127.0.0.1:7891 (running, web enabled)
- Config file: `./config.toml` (project root)
- Models on disk: base.en, base, medium, medium.en (test stubs), large-v3-turbo, silero-v6.2.0
- No API keys configured (cleanup/translation disabled)
- faster-whisper package: installed
- OS: Windows, Python 3.x venv

---

## Features

### F1 — Config round-trip (GET → render → collect → PUT)

**what**: Every config field loads from TOML, displays in the UI, and saves back without data loss or type coercion bugs.

**how-to-reach**: Open the UI, navigate each section, verify displayed values match `config.toml`, change a value, save, reload page, verify persistence.

**probes**:
- Change `hotkey.key` to a multi-modifier combo (`ctrl+shift+alt+space`) — does it round-trip?
- Set `audio.max_duration` to a float with decimals (e.g. `123.456`) — truncated or preserved?
- Set `streaming.interval_seconds` to very small (0.1) and very large (999) — accepted?
- Set `vocabulary.words` with commas, newlines, empty entries — deduped? Whitespace trimmed?
- Save with no changes — does the worker restart? (it shouldn't)
- Clear all vocabulary words → save → reload — is it `[]` not `[""]`?

**oracle**: `config.toml` on disk after save should match what the UI showed; `GET /api/config` response should match the UI state.

**risks**: Type coercion (JS `Number("")` → 0 vs null), list splitting on commas inside values, float precision loss in JS→JSON→TOML round-trip.

---

### F2 — Validation error surfacing

**what**: Invalid parameter values are rejected with clear error messages; the config file is not corrupted.

**how-to-reach**: Enter invalid values in various fields, click Save, observe toast/error behavior.

**probes**:
- Set `hotkey.mode` to an invalid string (manually edit the select or use API) — what happens?
- Set `whisper.server_url` to a non-URL (`not a url`) — validation error?
- Set `whisper.languages` to an invalid code (`xyz`) — error message mentions valid codes?
- Set `streaming.interval_seconds` to `0` or `-1` — rejected?
- Set `postprocess.trailing` to `"invalid"` — rejected?
- Set `vad.threshold` to `1.5` (out of range) — error?
- Set `vad.silence_duration` to `0` — error?
- Set `history.max_entries` to `-1` — error?
- Set `translation.target_language` to `auto` — error (auto not valid for translation)?
- Leave a numeric field blank and save — does it send `null` or `0`?

**oracle**: `_validate()` in `config.py` defines all constraints; error messages should match those `ValueError` strings.

**risks**: Client-side JS validation (`isNaN` check) may mask or duplicate server-side errors; blank numeric fields may silently become 0.

---

### F3 — Conditional field visibility (data-show-when)

**what**: Fields appear/hide correctly based on toggle state — no orphaned inputs submitted from hidden fields.

**how-to-reach**: Toggle checkboxes and dropdowns that gate other fields.

**probes**:
- Disable `whisper.managed` — does `server_bin` field hide?
- Enable `cleanup.enabled` — do provider/API key fields appear?
- Switch `cleanup.provider` between openai/anthropic — do the correct key/model fields swap?
- Disable `cleanup.enabled` while provider fields are visible — do they hide?
- Enable `streaming.enabled` — do engine/mode/interval fields appear?
- Switch `streaming.engine` to `faster_whisper` — do model/compute_type appear?
- Set `whisper.accent` to empty — does `accent_prompt` field hide?
- Set `whisper.accent` to `fr` — does `accent_prompt` appear?
- Enable `filler.enabled` — do `use_builtins` and custom fillers appear?
- Enable `translation.enabled` — does `target_language` appear?

**oracle**: `data-show-when` attribute conditions in the HTML; JS `applyVisibility()` function.

**risks**: Hidden fields with stale values being included in the save payload; visibility not updating on `change` events from selects.

---

### F4 — Save → restart behavior

**what**: Saving triggers the correct restart scope — worker-only for most changes, whisper-server restart for model/VAD changes, no restart for unchanged config.

**how-to-reach**: Make changes in different sections, save, observe the toast message and status dot transitions.

**probes**:
- Change `postprocess.trailing` → save — toast says "Saved — worker restarting…"?
- Change `whisper.model_path` (select a different model) → save — whisper_restarted = true? Status dot transitions (running → starting → running)?
- Change `vad.threshold` → save — does it restart whisper-server?
- Toggle `vad.enabled` → save — whisper-server restart?
- Save with zero changes (just click Save) — toast says "Saved." (no restart)?
- Change `overlay.enabled` → save — worker restart?
- Change only `history.max_entries` → save — worker restart (or should it not need one)?

**oracle**: `requires_restart()` in `webconfig.py` (returns True if old != new); `_vad_server_changed()` for whisper-server scope.

**risks**: Status dot stuck on "starting" if restart fails; ambiguity about which changes need whisper restart vs worker-only.

---

### F5 — Secret/API key handling

**what**: API keys are redacted in GET responses, preserved when unchanged, cleared on empty, and never leaked in error messages.

**how-to-reach**: Configure an API key, observe field behavior across save/reload cycles.

**probes**:
- Set `cleanup.openai.api_key` to a fake value (`sk-test123`), save, reload — field shows placeholder not the key?
- With saved key: leave password field unchanged, save — key preserved in TOML?
- With saved key: clear the field, save — key removed from TOML?
- With saved key: type a new value, save — old key replaced?
- Trigger a validation error with a key in the payload — error message does not contain the key value?
- Inspect `GET /api/config` response — keys show as `__SAMWHISPERS_SECRET_SET__`?

**oracle**: `SECRET_PATHS`, `REDACTED` sentinel in `webconfig.py`; `sanitize_secret_values()` for error messages.

**risks**: Redaction bypass via error messages; sentinel value accidentally saved to TOML; password field autofill interfering.

---

### F6 — Model management (select, download, delete)

**what**: Active model dropdown reflects disk state; downloads work with progress; delete is guarded.

**how-to-reach**: Navigate to Whisper Engine → Model section.

**probes**:
- Active model dropdown — lists all `.bin` files from models dir? Current selection highlighted?
- Select a different model from dropdown — does `whisper.model_path` input update?
- Click download on an already-downloaded model — button disabled?
- Click delete on a downloaded model (not active) — confirmation prompt? File removed?
- Click delete on the active model — error "Cannot delete active model"?
- Start a download — progress indicator updates? Final state shows "downloaded" tag?
- "Browse more models" — fetches HF list? Shows models not already on disk?
- Pin a custom model from discovery — appears in list with "custom" badge?

**oracle**: `GET /api/models` response; file existence on disk; `DELETE /api/models` guard logic.

**risks**: Download polling stops if page is navigated away; stale model list after download completes; race between download and delete.

---

### F7 — Snippets CRUD

**what**: Snippet trigger→expansion pairs can be added, edited, deleted, and saved.

**how-to-reach**: Navigate to Snippets page.

**probes**:
- Add a snippet ("my email" → "test@example.com") — row appears in table?
- Save → reload — snippet persists in TOML?
- Edit an existing snippet trigger — saved correctly?
- Delete a snippet row — removed from table and from saved config?
- Add a snippet with empty trigger — validation error?
- Add a snippet with empty expansion — validation error?
- Add a snippet with special characters (quotes, backslashes, newlines in expansion)?
- Snippet with `bias_recognition = true` — does it add triggers to vocabulary?

**oracle**: `[snippets.items]` in config.toml; validation in `_validate()`.

**risks**: TOML serialization of expansion strings with special chars (newlines as `\n` literal vs escaped); empty table row left behind after delete.

---

### F8 — VAD section

**what**: VAD parameters render correctly, validation works for range-bounded fields, model management works.

**how-to-reach**: Navigate to VAD page.

**probes**:
- With VAD model on disk — select shows the model path?
- Enable VAD → save — does `whisper-server` restart with `--vad` flag?
- Set `vad.threshold` to boundary values (0.0, 0.5, 1.0) — all accepted?
- Set `vad.threshold` to out-of-range (1.5, -0.1) — rejected?
- Set `silence_threshold` to boundary values — accepted/rejected correctly?
- Set `silence_duration` to 0 — rejected?
- Download VAD model button (if model were missing) — present?
- Delete VAD model while VAD enabled — guarded?

**oracle**: `_validate()` range checks; `DELETE /api/vad` guard; `POST /api/vad/download` flow.

**risks**: Numeric precision (0.001 steps) not transmitting correctly; model_path rendering for absolute Windows paths.

---

### F9 — Streaming section

**what**: Streaming parameters toggle correctly, conditional fields for faster_whisper engine appear, and engine/mode validation works.

**how-to-reach**: Navigate to Streaming page.

**probes**:
- Enable streaming — sub-fields appear?
- Switch engine to `faster_whisper` — model and compute_type fields appear?
- Switch engine back to `chunked` — faster_whisper fields hide?
- Set `interval_seconds` to 0 — rejected?
- Set `interval_seconds` to negative — rejected?
- Current config (`interval_seconds = 10`) — displays correctly?
- `output_mode` dropdown — shows `preview` and `progressive` options?
- Change `output_mode` to `preview` → save → reload — persisted?

**oracle**: `_VALID_STREAM_ENGINES`, `_VALID_STREAM_MODES` in config.py; `data-show-when` conditions.

**risks**: `interval_seconds` with very high values causing timeout; faster_whisper model select not syncing with text input.

---

### F10 — History retention settings + tab

**what**: History toggle and max_entries save correctly; History tab loads/searches/deletes entries.

**how-to-reach**: Navigate to History page (retention card + history list).

**probes**:
- Current `max_entries = 1000` — displayed correctly?
- Set to 0 (unlimited) — accepted?
- Set to -1 — rejected?
- Disable `history.enabled` → save → reload — persisted?
- History tab — shows transcription entries (if any exist)?
- Search box — filters entries?
- Delete single entry — removed?
- Clear all — confirmation prompt? All removed?
- Load more button — pagination works?

**oracle**: `GET /api/history` response; `HistoryStore` behavior; validation in `_validate()`.

**risks**: Clearing history is irreversible (no undo); search with special regex chars; empty state display.

---

### F11 — Logs tab

**what**: Log output renders correctly, error filtering works, auto-scroll behavior.

**how-to-reach**: Navigate to Logs page.

**probes**:
- Logs tab — shows recent log lines?
- "Errors only" filter — shows only ERROR/WARNING lines?
- After worker restart — new log lines appear?
- Long log output — scrollable container, not page overflow?
- "Jump to latest" button — scrolls to bottom?

**oracle**: `GET /api/logs` response; supervisor.logs ring buffer.

**risks**: Log container memory with very long sessions; XSS if log lines contain HTML-like content.

---

### F12 — Worker control buttons

**what**: Pause/Resume/Restart/Reload/RestartAll buttons work and reflect state.

**how-to-reach**: General page → action buttons.

**probes**:
- Click Pause — status dot turns amber? Button text changes to "Resume"?
- Click Resume — status dot turns green? Worker re-enters running state?
- Click "Restart worker" — worker restarts, status transitions visible?
- Click "Reload from disk" — config reloads from TOML without restart?
- Click "Restart SamWhispers" — full supervisor restart?

**oracle**: `POST /api/worker/{action}` responses; status dot CSS class changes.

**risks**: Double-click on restart causing race; "Restart SamWhispers" kills the web server (page becomes unreachable until restart completes).

---

### F13 — Autostart toggle

**what**: Start-at-login toggle reflects OS state and applies immediately.

**how-to-reach**: General page → Startup card (if supported).

**probes**:
- Card visibility — shown on Windows?
- Toggle on — Task Scheduler entry created?
- Toggle off — entry removed?
- Toggle with error (permissions) — error surfaced in toast?

**oracle**: `GET /api/autostart` supported/enabled state; `PUT /api/autostart` response.

**risks**: Requires elevated permissions on some Windows configs; section hidden if not supported.

---

### F14 — Translation section

**what**: Translation toggle and target language save correctly; validation rejects invalid targets.

**how-to-reach**: AI Processing page → Translation card.

**probes**:
- Enable translation — target_language dropdown appears?
- Dropdown excludes "auto"?
- Set target to a valid language (fr) → save → reload — persisted?
- Set target to "auto" via API — error?
- Enable translation without API key — warning?

**oracle**: `_validate()` check for `target_language != "auto"`; conditional visibility.

**risks**: Translation enabled without cleanup provider configured — unclear UX about shared keys.
