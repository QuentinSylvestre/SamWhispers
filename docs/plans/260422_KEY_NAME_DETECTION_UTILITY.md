# Key Name Detection Utility

> **Date**: 2026-04-22
> **Status**: Draft  <!-- Status lifecycle: Exploring → Draft → In Progress → Complete -->
> **Scope**: Standalone script to detect and display config-compatible key names for samwhispers hotkey configuration

---

## Intent

### Problem statement & desired outcomes
Users configuring samwhispers need to know the exact key name strings accepted by `config.toml` (`[hotkey] key`, `language_key`). There is no way to discover these names without reading the source code. A small standalone utility that listens for key presses and prints the config-compatible name solves this.

### Success criteria
- Standalone Python script runnable without installing samwhispers (just needs `pynput`)
- Continuous loop: press any key, see its config name, Ctrl+C to quit
- Output is minimal -- one line per key press
- Shows both the normalized name (e.g., `ctrl`) and the specific left/right variant (e.g., `ctrl_l`) when they differ
- Key names match exactly what `parse_hotkey()` in `hotkeys.py` accepts
- Works on Linux and Windows (no WSL backend needed)

### Scope boundaries & non-goals
- Individual key detection only, no combo detection
- Does not modify config -- read-only diagnostic tool
- No WSL/PowerShell backend
- No integration into the `samwhispers` CLI (standalone script for now)
- No config file reading or dependency on samwhispers internals beyond reusing the same key name vocabulary

## Context

SamWhispers uses `+`-separated key name strings in `config.toml` (e.g., `ctrl+shift+space`). The vocabulary is defined in `_SPECIAL_KEYS` (`src/samwhispers/hotkeys.py:14-46`) and consumed by `parse_hotkey()` (`hotkeys.py:48-63`). Modifiers are normalized left (`ctrl_r` -> `ctrl_l`) by `_normalize_key()` (`hotkeys.py:66-79`). Users currently have no way to discover valid key names without reading source. This utility fills that gap.

Note: `_SPECIAL_KEYS` also accepts `"escape"` as an alias for `"esc"` -- this is a forward-only alias (pynput emits `Key.esc`, there is no `Key.escape`).

## Files to modify

| File | Change |
|---|---|
| `tools/keyname.py` (new) | Standalone key name detection script |

## External Dependencies
None -- code-only change. Uses `pynput` which is already a project dependency.

## Rollout / Migration / Cleanup
None.

## Step-by-step

### 1. Build reverse mapping from pynput Key objects to config names

The script needs to translate pynput `Key` enum members and `KeyCode` objects back to the string names that `parse_hotkey()` accepts. Build two dicts:

- `_KEY_TO_SPECIFIC`: maps each pynput `Key` attribute to its specific name (e.g., `Key.ctrl_l` -> `"ctrl_l"`, `Key.ctrl_r` -> `"ctrl_r"`)
- `_KEY_TO_GENERIC`: maps pynput `Key` attributes to the shortest generic config name (e.g., `Key.ctrl_l` -> `"ctrl"`, `Key.ctrl_r` -> `"ctrl"`, `Key.space` -> `"space"`)

Derived from the same vocabulary as `_SPECIAL_KEYS` in `hotkeys.py:14-46`:

```python
import sys
from pynput.keyboard import Key, KeyCode, Listener

# Mirrors _SPECIAL_KEYS / _normalize_key() from hotkeys.py -- keep in sync
# Specific: every Key attr to its exact config name
_KEY_TO_SPECIFIC: dict[Key, str] = {
    Key.ctrl_l: "ctrl_l",
    Key.ctrl_r: "ctrl_r",
    Key.shift_l: "shift_l",
    Key.shift_r: "shift_r",
    Key.alt_l: "alt_l",
    Key.alt_r: "alt_r",
    Key.space: "space",
    Key.tab: "tab",
    Key.enter: "enter",
    Key.esc: "esc",
    Key.backspace: "backspace",
    Key.delete: "delete",
    Key.home: "home",
    Key.end: "end",
}
# Add F1-F12
for i in range(1, 13):
    _KEY_TO_SPECIFIC[getattr(Key, f"f{i}")] = f"f{i}"

# On some platforms pynput emits Key.ctrl / Key.alt / Key.shift (generic)
# instead of the _l variant. Map them the same way.  <!-- resolves review finding #1 -->
if hasattr(Key, "ctrl"):
    _KEY_TO_SPECIFIC.setdefault(Key.ctrl, "ctrl_l")
if hasattr(Key, "alt"):
    _KEY_TO_SPECIFIC.setdefault(Key.alt, "alt_l")
if hasattr(Key, "shift"):
    _KEY_TO_SPECIFIC.setdefault(Key.shift, "shift_l")

# Generic: maps L/R modifiers to the short form used in config
_KEY_TO_GENERIC: dict[Key, str] = dict(_KEY_TO_SPECIFIC)
_KEY_TO_GENERIC[Key.ctrl_l] = "ctrl"
_KEY_TO_GENERIC[Key.ctrl_r] = "ctrl"
_KEY_TO_GENERIC[Key.shift_l] = "shift"
_KEY_TO_GENERIC[Key.shift_r] = "shift"
_KEY_TO_GENERIC[Key.alt_l] = "alt"
_KEY_TO_GENERIC[Key.alt_r] = "alt"
if hasattr(Key, "ctrl"):
    _KEY_TO_GENERIC[Key.ctrl] = "ctrl"
if hasattr(Key, "alt"):
    _KEY_TO_GENERIC[Key.alt] = "alt"
if hasattr(Key, "shift"):
    _KEY_TO_GENERIC[Key.shift] = "shift"
```

For `KeyCode` objects (regular characters), use `key.char` if not None, otherwise print a "not supported" message.

### 2. Implement the listener and output logic

Use `pynput.keyboard.Listener` with an `on_press` callback. For each key press:

- If it's a `Key` enum member: look up both generic and specific names.
  - If they differ (modifier with L/R variant): print `generic (specific)` -- e.g., `ctrl (ctrl_r)`
  - If they're the same: print just the name -- e.g., `space`
- If it's a `KeyCode`: print `key.char` if not None, otherwise print a "not supported" note.
- If the key is a `Key` not in the mapping: print a clear "not supported in config" message using `key.name`.

<!-- resolves review findings #3, #4, #6, #7 -->
```python
def _on_press(key: Key | KeyCode | None) -> None:
    if isinstance(key, Key):
        specific = _KEY_TO_SPECIFIC.get(key)
        generic = _KEY_TO_GENERIC.get(key)
        if specific is None:
            # Key exists in pynput but not in samwhispers config vocabulary
            name = key.name if hasattr(key, "name") else repr(key)
            print(f"[{name}] -- not supported in config", flush=True)
            return
        if generic != specific:
            print(f"{generic} ({specific})", flush=True)
        else:
            print(specific, flush=True)
    elif isinstance(key, KeyCode):
        if key.char is not None:
            print(key.char, flush=True)
        else:
            print("[unknown key] -- not supported in config", flush=True)
```

### 3. Wire up the main loop with clean exit

<!-- resolves review findings #2, #5 -->
```python
def main() -> None:
    print("Press any key to see its config name. Ctrl+C to quit.")
    print("Use names in config.toml [hotkey] key, joined with +")
    print("Example: ctrl+shift+space")
    print("Keys are shown individually, not as combos.")
    print("Use the name before parentheses for config (e.g., ctrl, not ctrl_l).")
    print("---")
    try:
        with Listener(on_press=_on_press) as listener:
            listener.join()
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        print(
            f"Error: could not access keyboard: {exc}\n"
            "On Linux, this requires X11 (Wayland is not supported).\n"
            "You may need to run as root or add your user to the 'input' group.",
            file=sys.stderr,
        )
        sys.exit(1)

if __name__ == "__main__":
    main()
```

### 4. Add shebang and make self-contained

- Add `#!/usr/bin/env python3` shebang
- Single file, no imports from `samwhispers` package
- Only dependency: `pynput`

## Verification
```bash
# Run the script
python tools/keyname.py
# Press various keys and verify:
# - Ctrl shows: ctrl (ctrl_l) or ctrl (ctrl_r)
# - Shift shows: shift (shift_l) or shift (shift_r)
# - Alt shows: alt (alt_l) or alt (alt_r)
# - Space shows: space
# - F1 shows: f1
# - Letter 'a' shows: a
# - Ctrl+C exits cleanly
```

Also verify names match by cross-referencing with `parse_hotkey()` (separate manual check, not part of the script):
```python
# Quick sanity check -- the names printed by keyname.py
# should be accepted by parse_hotkey() without error
from samwhispers.hotkeys import parse_hotkey
parse_hotkey("ctrl+shift+space")  # should not raise
parse_hotkey("ctrl_r")            # should not raise
```

## Documentation updates
None -- this is a standalone tool in `tools/`. The README already mentions `tools/` as a directory for whisper.cpp; no update needed for an internal diagnostic script.

## Review Log

### 2026-04-22 -- Plan Review (Implementability + End-user advocate)

8 findings (1 High, 3 Medium, 4 Low). All auto-resolved.

| # | Finding | Severity | Status |
|---|---|---|---|
| 1 | `Key.ctrl`/`Key.alt` generic enum members missing from mapping (Windows emits these instead of `_l`) | High | Resolved -- added `hasattr` guards mapping generic Key members in Step 1 |
| 2 | No error handling for pynput access failures (Wayland, permissions) | High | Resolved -- added try/except with clear error message in Step 3 |
| 3 | `<unknown: repr>` output confusing -- use `key.name` and clearer message | Medium | Resolved -- changed to `[name] -- not supported in config` in Step 2 |
| 4 | `KeyCode` with `char=None` silently swallowed (numpad, dead keys) | Medium | Resolved -- now prints `[unknown key] -- not supported in config` in Step 2 |
| 5 | Startup message doesn't explain output format or combo assembly | Medium | Resolved -- expanded startup message with format explanation and example in Step 3 |
| 6 | `sys` import missing from code snippets | Low | Resolved -- added `import sys` in Step 1 imports |
| 7 | `key.char` truthiness check should be `is not None` | Low | Resolved -- changed to `key.char is not None` in Step 2 |
| 8 | Vocabulary drift risk (duplicated `_SPECIAL_KEYS` logic) | Low | Resolved -- added sync comment in Step 1 |
