#!/usr/bin/env python3
"""Detect key names for use in samwhispers config.toml.

Standalone utility -- only requires pynput. No samwhispers imports.
Press any key to see its config-compatible name. Ctrl+C to quit.

Mirrors the key vocabulary from samwhispers/hotkeys.py _SPECIAL_KEYS.
Keep the mappings below in sync with that dict.
"""

from __future__ import annotations

import sys
from typing import Union

from pynput.keyboard import Key, KeyCode, Listener  # type: ignore[import-untyped]

# ---------------------------------------------------------------------------
# Reverse mapping: pynput Key -> config name string
# Mirrors _SPECIAL_KEYS / _normalize_key() from hotkeys.py -- keep in sync
# ---------------------------------------------------------------------------

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
for _i in range(1, 13):
    _KEY_TO_SPECIFIC[getattr(Key, f"f{_i}")] = f"f{_i}"

# On some platforms pynput emits Key.ctrl / Key.alt / Key.shift (generic)
# instead of the _l variant. Map them the same way.
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


# ---------------------------------------------------------------------------
# Listener callback
# ---------------------------------------------------------------------------


def _on_press(key: Union[Key, KeyCode, None]) -> None:
    """Print the config-compatible name for a pressed key."""
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


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    """Run the key detection loop."""
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
