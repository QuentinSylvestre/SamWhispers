"""Global hotkey listener with hold and toggle modes."""

from __future__ import annotations

import logging
import subprocess
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
    """Parse a hotkey string like 'ctrl+shift+alt' into pynput key objects."""
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
        language_key_str: str | None = None,
        on_language_cycle: Callable[[], None] | None = None,
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
        self._lang_keys: set[Any] | None = None
        self._on_language_cycle = on_language_cycle
        if language_key_str and on_language_cycle:
            self._lang_keys = parse_hotkey(language_key_str)

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

            lang_match = self._lang_keys is not None and self._lang_keys.issubset(self._pressed)
            hotkey_match = self._hotkey_keys.issubset(self._pressed)

        if lang_match and self._on_language_cycle:
            self._on_language_cycle()
        elif hotkey_match:
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


# Windows Virtual Key codes for WSL hotkey listener
_VK_MAP: dict[str, int] = {
    "ctrl": 0xA2,  # VK_LCONTROL
    "ctrl_l": 0xA2,
    "ctrl_r": 0xA3,
    "shift": 0xA0,  # VK_LSHIFT
    "shift_l": 0xA0,
    "shift_r": 0xA1,
    "alt": 0xA4,  # VK_LMENU
    "alt_l": 0xA4,
    "alt_r": 0xA5,
    "space": 0x20,
    "tab": 0x09,
    "enter": 0x0D,
    "esc": 0x1B,
    "escape": 0x1B,
    "backspace": 0x08,
    "delete": 0x2E,
    "home": 0x24,
    "end": 0x23,
    "f1": 0x70,
    "f2": 0x71,
    "f3": 0x72,
    "f4": 0x73,
    "f5": 0x74,
    "f6": 0x75,
    "f7": 0x76,
    "f8": 0x77,
    "f9": 0x78,
    "f10": 0x79,
    "f11": 0x7A,
    "f12": 0x7B,
}


def parse_hotkey_vk(hotkey_str: str) -> list[int]:
    """Parse a hotkey string into Windows Virtual Key codes."""
    codes: list[int] = []
    for part in hotkey_str.lower().split("+"):
        part = part.strip()
        if part in _VK_MAP:
            codes.append(_VK_MAP[part])
        elif len(part) == 1:
            codes.append(ord(part.upper()))
        else:
            raise ValueError(f"Unknown key for WSL: {part!r}")
    return codes


class WSLHotkeyListener:
    """Detect push-to-talk hotkey on WSL via PowerShell GetAsyncKeyState polling."""

    def __init__(
        self,
        hotkey_str: str,
        mode: str,
        on_start: Callable[[], None],
        on_stop: Callable[[], None],
        language_key_str: str | None = None,
        on_language_cycle: Callable[[], None] | None = None,
    ) -> None:
        self._vk_codes = parse_hotkey_vk(hotkey_str)
        self._mode = mode
        self._on_start = on_start
        self._on_stop = on_stop
        self._suppressed = False
        self._lock = threading.Lock()
        self._process: Any = None
        self._thread: threading.Thread | None = None
        self._running = False
        self._lang_vk_codes: list[int] | None = None
        self._on_language_cycle = on_language_cycle
        if language_key_str and on_language_cycle:
            self._lang_vk_codes = parse_hotkey_vk(language_key_str)

    def start(self) -> None:
        """Start the PowerShell hotkey polling subprocess."""
        import subprocess

        from samwhispers.wsl import find_windows_exe

        ps = find_windows_exe("powershell.exe")
        if not ps:
            raise RuntimeError("powershell.exe not found for WSL hotkey listener")

        # Build PowerShell script that polls GetAsyncKeyState
        vk_array = ",".join(f"0x{c:02X}" for c in self._vk_codes)

        # Language hotkey polling (optional)
        lang_section = ""
        if self._lang_vk_codes:
            lang_vk_array = ",".join(f"0x{c:02X}" for c in self._lang_vk_codes)
            lang_section = f"$langKeys = @({lang_vk_array})\n$langDown = $false\n"
            lang_loop = (
                "    $allLang = $true\n"
                "    foreach ($k in $langKeys) {\n"
                "        if (([KS]::GetAsyncKeyState($k) -band 0x8000) -eq 0) {\n"
                "            $allLang = $false; break\n"
                "        }\n"
                "    }\n"
                '    if ($allLang -and -not $langDown) { $langDown = $true; Write-Output "LANG"; [Console]::Out.Flush() }\n'
                "    elseif (-not $allLang -and $langDown) { $langDown = $false }\n"
            )
        else:
            lang_loop = ""

        script = (
            'Add-Type -TypeDefinition @"\n'
            "using System;\n"
            "using System.Runtime.InteropServices;\n"
            "public class KS {\n"
            '    [DllImport("user32.dll")]\n'
            "    public static extern short GetAsyncKeyState(int vKey);\n"
            "}\n"
            '"@\n'
            f"$keys = @({vk_array})\n"
            "$down = $false\n"
            f"{lang_section}"
            "while ($true) {\n"
            "    $all = $true\n"
            "    foreach ($k in $keys) {\n"
            "        if (([KS]::GetAsyncKeyState($k) -band 0x8000) -eq 0) {\n"
            "            $all = $false; break\n"
            "        }\n"
            "    }\n"
            '    if ($all -and -not $down) { $down = $true; Write-Output "PRESS"; [Console]::Out.Flush() }\n'
            '    elseif (-not $all -and $down) { $down = $false; Write-Output "RELEASE"; [Console]::Out.Flush() }\n'
            f"{lang_loop}"
            "    Start-Sleep -Milliseconds 15\n"
            "}\n"
        )

        self._running = True
        self._process = subprocess.Popen(
            [ps, "-NoProfile", "-c", script],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._thread.start()
        log.info("WSL hotkey listener started (mode=%s, polling via GetAsyncKeyState)", self._mode)

    def _read_loop(self) -> None:
        """Read PRESS/RELEASE/LANG events from PowerShell subprocess."""
        active = False
        while self._running:
            proc = self._process
            if not proc or not proc.stdout:
                break
            line = proc.stdout.readline().strip()
            if not line:
                if proc.poll() is not None:
                    break
                continue

            with self._lock:
                if self._suppressed:
                    continue

            if line == "LANG":
                if self._on_language_cycle:
                    self._on_language_cycle()
                continue

            if self._mode == "hold":
                if line == "PRESS":
                    self._on_start()
                elif line == "RELEASE":
                    self._on_stop()
            else:  # toggle
                if line == "PRESS":
                    if active:
                        active = False
                        self._on_stop()
                    else:
                        active = True
                        self._on_start()

    def stop(self) -> None:
        """Stop the listener."""
        self._running = False
        if self._process:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=10)
            self._process = None
        log.debug("WSL hotkey listener stopped")

    def suppress(self) -> None:
        with self._lock:
            self._suppressed = True

    def resume(self) -> None:
        with self._lock:
            self._suppressed = False
