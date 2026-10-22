"""Workstation lock detection.

While the lock screen is up, the input desktop is Winlogon's secure desktop: this process
either cannot open it or cannot switch to it. Unlocked, switching to the current input desktop
is a no-op that succeeds.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from luxstatus.state import Source, SourceUpdate, Status

DESKTOP_SWITCHDESKTOP = 0x0100


def workstation_locked() -> bool:
    import ctypes

    user32 = ctypes.windll.user32
    desktop = user32.OpenInputDesktop(0, False, DESKTOP_SWITCHDESKTOP)
    if not desktop:
        return True
    try:
        return not user32.SwitchDesktop(desktop)
    finally:
        user32.CloseDesktop(desktop)


class SessionSource:
    def __init__(self, is_locked: Callable[[], bool] = workstation_locked) -> None:
        self.is_locked = is_locked

    def poll(self, now: datetime) -> SourceUpdate:
        if self.is_locked():
            return SourceUpdate(Source.SESSION, now, Status.OFF, "Locked")
        return SourceUpdate(Source.SESSION, now)
