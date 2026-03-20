"""Global hotkey listener with hold and toggle modes."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

log = logging.getLogger("samwhispers")

# Mapping of common key names to pynput Key attributes
_SPECIAL_KEYS: dict[str, str] = {
    "ctrl": "ctrl_l",
    "ctrl_l": "ctrl_l",
    "ctrl_r": "ctrl_r",
    "shift": "shift_l",
    "shift_l": "shift_l",
    "shift_r": "shift_r",
    "alt": "alt_l",
    "alt_l": "alt_l",
    "alt_r": "alt_r",
    "space": "space",
    "tab": "tab",
    "enter": "enter",
    "esc": "esc",
    "escape": "esc",
    "backspace": "backspace",
    "delete": "delete",
    "home": "home",
    "end": "end",
    "f1": "f1",
    "f2": "f2",
    "f3": "f3",
    "f4": "f4",
    "f5": "f5",
    "f6": "f6",
    "f7": "f7",
    "f8": "f8",
    "f9": "f9",
    "f10": "f10",
    "f11": "f11",
    "f12": "f12",
}


def parse_hotkey(hotkey_str: str) -> set[Any]:
    """Parse a hotkey string like 'ctrl+shift+space' into pynput key objects."""
    from pynput.keyboard import Key, KeyCode  # type: ignore[import-untyped]

    keys: set[Key | KeyCode] = set()
    for part in hotkey_str.lower().split("+"):
        part = part.strip()
        if part in _SPECIAL_KEYS:
            keys.add(getattr(Key, _SPECIAL_KEYS[part]))
        elif len(part) == 1:
            keys.add(KeyCode.from_char(part))
        else:
            raise ValueError(f"Unknown key: {part!r}")
    return keys


def _normalize_key(key: Any) -> Any:
    """Normalize a key event to match parsed hotkey keys.

    Maps ctrl_r -> ctrl_l, shift_r -> shift_l, alt_r -> alt_l so that
    either side of a modifier matches the hotkey definition.
    """
    from pynput.keyboard import Key

    _ALIASES = {
        Key.ctrl_r: Key.ctrl_l,
        Key.shift_r: Key.shift_l,
        Key.alt_r: Key.alt_l,
    }
    return _ALIASES.get(key, key)


class HotkeyListener:
    """Detect push-to-talk hotkey in hold or toggle mode."""

    def __init__(
        self,
        hotkey_str: str,
        mode: str,
        on_start: Callable[[], None],
        on_stop: Callable[[], None],
    ) -> None:
        self._hotkey_keys = parse_hotkey(hotkey_str)
        self._mode = mode
        self._on_start = on_start
        self._on_stop = on_stop
        self._pressed: set[Any] = set()
        self._active = False  # Toggle state
        self._suppressed = False
        self._lock = threading.Lock()
        self._listener: Any = None

    def start(self) -> None:
        """Start the hotkey listener (non-blocking daemon thread)."""
        from pynput.keyboard import Listener

        self._listener = Listener(on_press=self._on_press, on_release=self._on_release)
        self._listener.daemon = True
        self._listener.start()
        log.info("Hotkey listener started (mode=%s)", self._mode)

    def stop(self) -> None:
        """Stop the listener."""
        if self._listener:
            self._listener.stop()
            self._listener = None
        log.debug("Hotkey listener stopped")

    def suppress(self) -> None:
        """Temporarily ignore hotkey events (during paste)."""
        with self._lock:
            self._suppressed = True

    def resume(self) -> None:
        """Resume hotkey detection after paste."""
        with self._lock:
            self._suppressed = False
            self._pressed.clear()

    def _on_press(self, key: Any) -> None:
        with self._lock:
            if self._suppressed:
                return
            normalized = _normalize_key(key)
            if normalized in self._pressed:
                return  # Filter key repeat
            self._pressed.add(normalized)

            if not self._hotkey_keys.issubset(self._pressed):
                return

        if self._mode == "hold":
            self._on_start()
        else:  # toggle
            with self._lock:
                if self._active:
                    self._active = False
                    self._on_stop()
                else:
                    self._active = True
                    self._on_start()

    def _on_release(self, key: Any) -> None:
        with self._lock:
            if self._suppressed:
                return
            normalized = _normalize_key(key)
            self._pressed.discard(normalized)

        if self._mode == "hold" and normalized in self._hotkey_keys:
            self._on_stop()
