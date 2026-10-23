"""Global hotkeys via Win32 RegisterHotKey on a dedicated message-loop thread.

RegisterHotKey avoids a system-wide keyboard hook, which security software tends to flag.
Parsing is pure; the thread uses ctypes and only works on Windows.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

log = logging.getLogger(__name__)

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012

_MODIFIERS = {
    "ctrl": MOD_CONTROL,
    "control": MOD_CONTROL,
    "alt": MOD_ALT,
    "shift": MOD_SHIFT,
    "win": MOD_WIN,
    "cmd": MOD_WIN,
}


def parse_hotkey(text: str) -> tuple[int, int]:
    """``<ctrl>+<alt>+d`` or ``ctrl+alt+F9`` → (modifier flags, virtual-key code)."""
    parts = [p.strip().strip("<>").lower() for p in text.split("+") if p.strip()]
    if not parts:
        raise ValueError(f"empty hotkey {text!r}")
    *mods, key = parts
    flags = MOD_NOREPEAT
    for m in mods:
        if m not in _MODIFIERS:
            raise ValueError(f"unknown modifier {m!r} in hotkey {text!r}")
        flags |= _MODIFIERS[m]
    if flags == MOD_NOREPEAT:
        raise ValueError(f"hotkey {text!r} needs at least one modifier")
    return flags, _vk(key, text)


def _vk(key: str, text: str) -> int:
    if len(key) == 1 and (key.isascii() and key.isalnum()):
        return ord(key.upper())
    if key.startswith("f") and key[1:].isdigit() and 1 <= int(key[1:]) <= 24:
        return 0x70 + int(key[1:]) - 1
    raise ValueError(f"unsupported key {key!r} in hotkey {text!r} (use a-z, 0-9 or F1-F24)")


class HotkeyThread(threading.Thread):
    """Registers hotkeys and calls their callbacks on this thread. A hotkey that another app
    already owns is logged and skipped."""

    def __init__(self, bindings: dict[str, Callable[[], None]]) -> None:
        super().__init__(name="hotkeys", daemon=True)
        self.bindings = bindings
        self._thread_id: int | None = None
        self._ready = threading.Event()

    def run(self) -> None:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        self._thread_id = kernel32.GetCurrentThreadId()
        callbacks: dict[int, Callable[[], None]] = {}
        for i, (text, callback) in enumerate(self.bindings.items(), start=1):
            try:
                mods, vk = parse_hotkey(text)
            except ValueError as e:
                log.warning("hotkey ignored: %s", e)
                continue
            if user32.RegisterHotKey(None, i, mods, vk):
                callbacks[i] = callback
                log.info("hotkey %s registered", text)
            else:
                log.warning("hotkey %s is already in use by another app; skipped", text)

        msg = wintypes.MSG()
        # Make sure this thread has a message queue before stop() can post WM_QUIT to it,
        # even when no hotkey was registered.
        PM_NOREMOVE = 0x0000
        user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_NOREMOVE)
        self._ready.set()

        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == WM_HOTKEY and msg.wParam in callbacks:
                    try:
                        callbacks[msg.wParam]()
                    except Exception:
                        log.exception("hotkey callback failed")
        finally:
            for i in callbacks:
                user32.UnregisterHotKey(None, i)

    def stop(self) -> None:
        self._ready.wait(timeout=2)
        if self._thread_id is not None:
            import ctypes

            ctypes.windll.user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        self.join(timeout=2)
