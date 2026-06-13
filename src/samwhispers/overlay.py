"""Floating on-screen recording/transcription indicator.

Runs as its own process (``python -m samwhispers.overlay``) spawned by the
worker, which streams ``{"state", "level"}`` messages to its stdin. Keeping it
out-of-process avoids running a Tk event loop inside the worker and means the
audio callback never blocks on UI I/O.

While recording it shows a few white bars whose heights track the audio level;
while transcribing it shows a spinner; when idle the window hides. Tkinter is
imported lazily so this module (and the worker's ``OverlayController``) import
cleanly on hosts without Tk or a display.
"""

from __future__ import annotations

import json
import logging
import math
import os
import subprocess
import sys
import threading
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import tkinter as tk

log = logging.getLogger("samwhispers.overlay")

# Window / drawing constants
_W, _H = 150, 46
_MARGIN = 80  # px above the bottom of the screen
_N_BARS = 4
_BAR_W = 6
_BAR_GAP = 10
_BAR_MIN, _BAR_MAX = 6, 30
_PILL = "#2c2c2e"  # dark glass grey (translucency via window -alpha)
_TRANSPARENT_KEY = "#010203"  # color knocked out on Windows for rounded corners
_FPS = 30

# Relative bar weighting so the middle bars reach higher (a livelier shape).
_BAR_WEIGHTS = (0.65, 1.0, 0.85, 0.7)


def bottom_center_geometry(
    screen_w: int, screen_h: int, w: int = _W, h: int = _H, margin: int = _MARGIN
) -> tuple[int, int]:
    """Top-left (x, y) to place a w*h window centered near the screen bottom."""
    x = (screen_w - w) // 2
    y = screen_h - h - margin
    return x, max(0, y)


def bar_targets(level: float, n: int = _N_BARS) -> list[float]:
    """Target heights (0..1) for ``n`` bars given a 0..1 audio level."""
    level = min(1.0, max(0.0, level))
    weights = _BAR_WEIGHTS if n == len(_BAR_WEIGHTS) else (1.0,) * n
    base = 0.18  # bars never fully collapse while recording
    return [min(1.0, base + level * weights[i] * (1.0 - base)) for i in range(n)]


def _display_available() -> bool:
    """Whether a GUI display is likely usable for the overlay."""
    if sys.platform in ("win32", "darwin"):
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


class OverlayController:
    """Worker-side handle: spawns the overlay process and streams it updates.

    ``set_state``/``set_level`` only update fields; a background thread sends
    them to the overlay at ~30fps so the audio callback never does UI I/O.
    """

    def __init__(self) -> None:
        self._proc: subprocess.Popen[str] | None = None
        self._lock = threading.Lock()
        self._state = "idle"
        self._level = 0.0
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Spawn the overlay process (no-op without a display or on failure)."""
        if not _display_available():
            log.info("No display detected; overlay disabled")
            return
        try:
            self._proc = subprocess.Popen(
                [sys.executable, "-m", "samwhispers.overlay"],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
            )
        except Exception:
            log.exception("Failed to start overlay process")
            return
        self._thread = threading.Thread(target=self._sender, daemon=True, name="overlay-sender")
        self._thread.start()

    def set_state(self, state: str) -> None:
        with self._lock:
            self._state = state
            if state == "idle":
                self._level = 0.0
        self._wake.set()

    def set_level(self, level: float) -> None:
        with self._lock:
            self._level = level

    def _sender(self) -> None:
        idle_sent = False
        while not self._stop.is_set():
            proc = self._proc
            if proc is None or proc.poll() is not None:
                return
            with self._lock:
                state, level = self._state, self._level
            if state == "idle":
                if not idle_sent and not self._write({"state": "idle", "level": 0.0}):
                    return
                idle_sent = True
                self._wake.wait(0.2)
                self._wake.clear()
                continue
            idle_sent = False
            if not self._write({"state": state, "level": round(level, 3)}):
                return
            time.sleep(1.0 / _FPS)

    def _write(self, msg: dict[str, Any]) -> bool:
        proc = self._proc
        if proc is None or proc.stdin is None:
            return False
        try:
            proc.stdin.write(json.dumps(msg) + "\n")
            proc.stdin.flush()
            return True
        except Exception:
            log.debug("Overlay write failed; disabling", exc_info=True)
            return False

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        with self._lock:
            proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            if proc.stdin is not None:
                proc.stdin.close()
        except Exception:
            pass
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=2.0)
            except Exception:
                proc.kill()


class OverlayApp:
    """The Tk window: animated bars (recording) or spinner (transcribing)."""

    def __init__(self, root: tk.Tk) -> None:
        import tkinter as tk

        self.root = root
        self._lock = threading.Lock()
        self._state = "idle"
        self._level = 0.0
        self._display_level = 0.0
        self._spin = 0
        self._t = 0
        self._visible = False

        root.overrideredirect(True)
        root.attributes("-topmost", True)
        try:
            root.attributes("-alpha", 0.9)
        except Exception:
            pass

        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        x, y = bottom_center_geometry(sw, sh)
        root.geometry(f"{_W}x{_H}+{x}+{y}")

        bg = _TRANSPARENT_KEY if sys.platform == "win32" else _PILL
        if sys.platform == "win32":
            try:
                root.attributes("-transparentcolor", _TRANSPARENT_KEY)
            except Exception:
                bg = _PILL
        root.configure(bg=bg)

        self.canvas: Any = tk.Canvas(root, width=_W, height=_H, bg=bg, highlightthickness=0)
        self.canvas.pack()
        self._draw_pill(bg != _PILL)

        cx, cy = _W // 2, _H // 2
        group_w = _N_BARS * _BAR_W + (_N_BARS - 1) * _BAR_GAP
        x0 = cx - group_w // 2
        self._bars = []
        for i in range(_N_BARS):
            bx = x0 + i * (_BAR_W + _BAR_GAP)
            item = self.canvas.create_rectangle(
                bx, cy - 4, bx + _BAR_W, cy + 4, fill="#ffffff", outline=""
            )
            self._bars.append((item, bx))
        self._arc = self.canvas.create_arc(
            cx - 12,
            cy - 12,
            cx + 12,
            cy + 12,
            start=0,
            extent=270,
            style=tk.ARC,
            outline="#ffffff",
            width=3,
            state="hidden",
        )

        root.withdraw()
        reader = threading.Thread(target=self._read_stdin, daemon=True, name="overlay-reader")
        reader.start()
        self._tick()

    def _draw_pill(self, rounded: bool) -> None:
        """Draw the glass background. Rounded corners only where keyed-out (Windows)."""
        if rounded:
            r = _H // 2
            self.canvas.create_oval(0, 0, _H, _H, fill=_PILL, outline="")
            self.canvas.create_oval(_W - _H, 0, _W, _H, fill=_PILL, outline="")
            self.canvas.create_rectangle(r, 0, _W - r, _H, fill=_PILL, outline="")
        # On non-Windows the canvas bg already is the pill colour (translucent
        # via -alpha), so nothing extra to draw.

    def _read_stdin(self) -> None:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            with self._lock:
                self._state = str(msg.get("state", self._state))
                self._level = float(msg.get("level", 0.0))
        # Parent closed the pipe -> exit.
        try:
            self.root.after(0, self.root.destroy)
        except Exception:
            pass

    def _tick(self) -> None:
        self._t += 1
        with self._lock:
            state, level = self._state, self._level

        if state == "idle":
            if self._visible:
                self.root.withdraw()
                self._visible = False
            self.root.after(int(1000 / _FPS), self._tick)
            return

        if not self._visible:
            self.root.deiconify()
            self.root.attributes("-topmost", True)
            self._visible = True

        if state == "processing":
            self._animate_spinner()
        else:
            self._animate_bars(level)

        self.root.after(int(1000 / _FPS), self._tick)

    def _animate_bars(self, level: float) -> None:
        self.canvas.itemconfigure(self._arc, state="hidden")
        self._display_level += (level - self._display_level) * 0.35
        targets = bar_targets(self._display_level)
        cy = _H // 2
        for i, (item, bx) in enumerate(self._bars):
            osc = 0.12 * math.sin(self._t * 0.4 + i) * self._display_level
            frac = min(1.0, max(0.0, targets[i] + osc))
            h = _BAR_MIN + frac * (_BAR_MAX - _BAR_MIN)
            self.canvas.itemconfigure(item, state="normal")
            self.canvas.coords(item, bx, cy - h / 2, bx + _BAR_W, cy + h / 2)

    def _animate_spinner(self) -> None:
        for item, _ in self._bars:
            self.canvas.itemconfigure(item, state="hidden")
        self._spin = (self._spin - 12) % 360
        self.canvas.itemconfigure(self._arc, state="normal", start=self._spin)


def main() -> None:
    """Run the overlay window. Exits quietly if no display is available."""
    try:
        import tkinter as tk

        root = tk.Tk()
    except Exception as exc:  # pragma: no cover - needs a display
        log.info("Overlay unavailable: %s", exc)
        return
    OverlayApp(root)
    try:
        root.mainloop()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
