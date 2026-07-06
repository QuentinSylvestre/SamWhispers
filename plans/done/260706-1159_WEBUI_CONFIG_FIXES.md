# Web UI Config Parameter Fixes

> **Date**: 2026-07-06
> **Status**: Complete  <!-- All 4 SCs verified green, 2026-07-06 -->
> **Last Updated**: 2026-07-06 11:59
> **Scope**: Fix 4 verified findings from /qtest run — model delete button, missing save button, global collect, unsurfaced warnings

---

## Intent

### Problem statement & desired outcomes

The `/qtest run` on the config web UI surfaced 4 verified defects that degrade the parameter-editing UX:

1. **Active model delete button enabled** — the model list shows an enabled delete button for the active model; clicking it yields an HTTP 409 error instead of being visually disabled.
2. **History page missing Save button** — the History tab contains config fields (`history.enabled`, `history.max_entries`) but no Save button; users must navigate to another tab to save.
3. **Global collect/validate blocks cross-section saves** — all Save buttons collect and validate every field document-wide, so an invalid value on any hidden page blocks saving from the current page.
4. **Backend warnings not surfaced** — `warnings.warn()` in validation (missing API key for cleanup/translation) is silently swallowed; the user sees "Saved" with no hint their feature won't work.

Desired outcome: all 4 issues resolved, re-verifiable via `/qtest run`.

### Success criteria

- SC1: Delete button is disabled for the model whose path matches `whisper.model_path`.
- SC2: History page has a savebar with a functioning Save button.
- SC3: `collect()` and client-side validation are scoped to the active page only; saving from one section does not validate hidden sections.
- SC4: `PUT /api/config` returns a `warnings` field; the UI displays non-empty warnings as amber toasts.

### Scope boundaries & non-goals

**In scope**: `src/samwhispers/web/index.html` (HTML + JS), `src/samwhispers/webserver.py` (PUT handler), `src/samwhispers/webconfig.py` (save path).

**Non-goals**: Refactoring the SPA into a framework; adding per-field inline validation; changing the config validation logic itself (the `warnings.warn()` vs `raise ValueError` boundary stays as-is).

## Context

Four verified findings from a `/qtest run` on the config web UI. All are UX defects — no data loss or security issues. The web UI is a single-file SPA (`index.html`) backed by a FastAPI server (`webserver.py`). Config validation lives in `config.py` and uses `warnings.warn()` for non-blocking advisories and `raise ValueError` for hard failures.

## Files to modify

| File | Change |
|---|---|
| `src/samwhispers/web/index.html` | SC1: disable delete button for active model in `renderModelManager()` |
| `src/samwhispers/web/index.html` | SC2: add savebar to `#page-history` |
| `src/samwhispers/web/index.html` | SC3: scope `collect()` and `save()` validation to `.page.active` |
| `src/samwhispers/web/index.html` | SC4: read `warnings` from PUT response; show amber toasts |
| `src/samwhispers/webserver.py` | SC4: capture `warnings.warn()` during save and return in response |

## External Dependencies

None — code-only changes to the local web UI.

## Rollout / Migration / Cleanup

None — no persisted data changes, no operator actions.

## Step-by-step

### 1. Disable delete button for the active model (SC1) [QA]

In `renderModelManager()` (~line 689), determine whether each model is the active one and disable its delete button accordingly.

**Current code** (index.html ~line 700):
```js
`<button class="del-btn" title="Delete" ${isDl ? "" : "disabled"}>&#x1F5D1;</button>`
```

**Change**: add an `isActive` check. Use exact filename comparison (not substring `includes()`) to avoid false positives — e.g. `base.bin` matching inside `base.en.bin`:
```js
const modelPath = document.querySelector('[data-path="whisper.model_path"]')?.value || "";
const activeFilename = modelPath.split(/[\\/]/).pop() || "";  // extract "ggml-base.en.bin"
// inside the loop:
const isActive = activeFilename === `ggml-${name}.bin`;
const delDisabled = !isDl || isActive;
// ...
`<button class="del-btn" title="Delete${isActive ? " (active)" : ""}" ${delDisabled ? "disabled" : ""}>&#x1F5D1;</button>`
```

Also apply the same check to custom pinned models (~line 713):
```js
const isActiveCustom = activeFilename === filename;
```

### 2. Add Save button to History page (SC2) [QA]

Insert a savebar inside `#page-history` after the Retention card, before the `.histbar`:

```html
<div class="savebar">
  <button class="primary" id="btnSaveHistory">Save</button>
  <span class="hint">The worker restarts automatically when a change requires it.</span>
</div>
```

The existing JS at ~line 878 (`for (const btn of document.querySelectorAll('[id^="btnSave"]'))`) will auto-wire this button to the `save()` handler — no additional JS needed.

### 3. Scope collect() and validation to active page (SC3) [QA]

**3a. Scope `collect()`**: change the selector from document-wide to active-page only:

```js
function collect() {
  const scope = document.querySelector(".page.active") || document;
  for (const el of scope.querySelectorAll("[data-path]")) {
    // ... existing logic unchanged
  }
  // collectSnippets() only writes if the snippets page is active
  if (document.getElementById("page-snippets")?.classList.contains("active")) {
    collectSnippets();
  }
}
```

This is safe because `state.config` retains the full config from the last `GET /api/config` load. The active page's DOM values overwrite their respective paths; other pages' values remain as loaded.

**3b. Scope client-side validation**: scope the numeric-field validation loop similarly:

```js
async function save() {
  const scope = document.querySelector(".page.active") || document;
  for (const el of scope.querySelectorAll("[data-path][data-type='number']")) {
    // ... existing validation logic unchanged
  }
  collect();
  // ... rest unchanged
}
```

**3c. Fix `updateDirtyState()` interaction** (~line 1321):

The existing `updateDirtyState()` calls `collect()` globally to compare DOM state against `cleanConfig`. After scoping `collect()`, this still works correctly: it compares only the active page's fields (the only ones the user can currently edit). The dirty indicator reflects whether the *active* page has unsaved changes relative to the last save/load — which aligns with the new per-page save semantic.

No change needed to `updateDirtyState()` itself — its behavior naturally follows `collect()`'s scope. After a page switch, `updateDirtyState()` will fire (via the `change` listener or call in `render`) and correctly reflect the new page's clean state.

**Accepted trade-off**: A user who edits Page A, switches to Page B without saving, then saves from Page B — Page A's edits remain only in the DOM (not in `state.config`). When they switch back to Page A and save, those DOM values are collected and saved. This is the standard per-section save UX — no data is lost unless the page is reloaded.

**3d. Handle `collectSnippets()` in the snippet Save button**: The snippets page's `btnSaveSnippets` click handler currently calls `collectSnippets()` before `save()`. Since the scoped `collect()` already gates `collectSnippets()` on snippets page being active, this produces a harmless double-invocation (idempotent). Leave as-is for clarity.

### 4. Surface backend warnings in PUT response (SC4) [QA]

**4a. Backend** (`webserver.py`, `put_config` handler ~line 345):

Wrap the save call with `warnings.catch_warnings()`:

```python
import warnings as _warnings

# inside put_config:
with _warnings.catch_warnings(record=True) as caught:
    _warnings.simplefilter("always")
    try:
        old_cfg = current_app_config(config_path)
        new_cfg = save_config_dict(payload, config_path)
    except ValueError as exc:
        detail = _safe_config_error(str(exc), config_path, payload)
        raise HTTPException(status_code=400, detail=detail) from exc
    except OSError as exc:
        detail = _safe_config_error(str(exc), config_path, payload)
        raise HTTPException(status_code=500, detail=f"Config save failed: {detail}") from exc

warn_messages = [str(w.message) for w in caught if issubclass(w.category, UserWarning)]

# ... restart logic unchanged ...

return {"saved": True, "restarted": restarted, "whisper_restarted": whisper_restarted, "warnings": warn_messages}
```

Note: `catch_warnings(record=True)` is thread-safe on Python 3.11+ (per-thread warning filters). The project requires Python 3.11+ (see pyproject.toml), so this is safe.

**4b. Frontend** (`index.html`, `save()` ~line 893):

After the success toast, show warning toasts (concatenated into one amber toast with 4s timeout):

```js
const r = await api("PUT", "/api/config", state.config);
toast(r.restarted ? "Saved \u2014 worker restarting\u2026" : "Saved.", "ok");
if (r.warnings && r.warnings.length) {
  toast("Warning: " + r.warnings.join(" · "), "warn");
}
```

Add a `.warn` toast style (amber border) and use the same 4s timeout as errors:
```css
.toast.warn { border-color: var(--amber); }
```

Update the toast timeout logic to use 4s for both `"err"` and `"warn"`:
```js
setTimeout(() => (t.className = "toast"), (kind === "err" || kind === "warn") ? 4000 : 2000);
```

## Verification

- Run `/qtest run` against the updated UI — all 4 findings should be resolved.
- Add a pytest test for `PUT /api/config` verifying the `warnings` field is returned when translation is enabled without an API key.
- Manual checks:
  - Navigate to Whisper Engine → verify delete button is disabled for the active model (exact filename match, not substring).
  - Navigate to History → verify Save button exists and saves `max_entries` changes.
  - Set an invalid value on one page → navigate to another page → verify Save works from the other page.
  - Enable Translation without API key → Save → verify amber toast with warning message appears (4s duration).
  - Edit a field → verify dirty indicator ("Save •") appears only on active page's Save button.
  - Edit Page A → switch to Page B → Save from B → switch back to A → verify A's DOM edits are still present (not lost until page reload).

## Documentation updates

None — these are internal UI fixes with no user-facing documentation impact. The README describes the web UI at a feature level; these fixes don't change behavior descriptions.


## Review Log

### 2026-07-06 -- Plan Review (High effort, 3 personas: Senior engineer, End-user advocate, Reliability engineer)

8 findings (1 High, 4 Medium, 3 Low). 7 auto-resolved.

| # | Severity | Finding (one line) | Status (one line) |
|---|---|---|---|
| 1 | High | SC3 scoping breaks `updateDirtyState()` which calls `collect()` globally | Resolved — documented that dirty-state naturally follows scoped collect; no regression |
| 2 | Medium | SC3 silently drops unsaved edits on non-active pages | Resolved — documented as accepted trade-off; DOM edits preserved until reload |
| 3 | Medium | SC1 `includes()` substring match false-positive risk | Resolved — changed to exact filename comparison via `split(/[\\/]/).pop()` |
| 4 | Medium | SC4 `catch_warnings` thread-safety on Python <3.11 | Resolved — noted project requires Python 3.11+ |
| 5 | Low | SC4 toast timeout too short for warnings | Resolved — 4s timeout for `"warn"` kind |
| 6 | Low | SC4 multiple warnings could stack | Resolved — concatenate into single amber toast |
| 7 | Low | Missing test for SC4 warning capture | Resolved — added pytest requirement to Verification |
| 8 | Low | `collectSnippets()` double-invocation | Noted — idempotent, harmless, left as-is for clarity |
