"""Manual override state, driven by the tray menu and hotkeys. The resolver checks expiry."""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import datetime, timedelta

from luxstatus.state import MANUAL_STATUSES, Source, SourceUpdate, Status


class ManualOverride:
    def __init__(self) -> None:
        self.status: Status | None = None
        self.until: datetime | None = None

    def active(self, now: datetime) -> bool:
        return self.status is not None and (self.until is None or now < self.until)

    def set(self, status: Status, now: datetime, duration: timedelta | None = None) -> SourceUpdate:
        if status not in MANUAL_STATUSES:
            raise ValueError(f"manual override cannot be {status.name}")
        self.status = status
        self.until = now + duration if duration is not None else None
        return self.update(now)

    def clear(self, now: datetime) -> SourceUpdate:
        """Return to Auto."""
        self.status = None
        self.until = None
        return self.update(now)

    def toggle_dnd(self, now: datetime) -> SourceUpdate:
        if self.active(now) and self.status is Status.DND:
            return self.clear(now)
        return self.set(Status.DND, now)

    def update(self, now: datetime) -> SourceUpdate:
        return SourceUpdate(Source.MANUAL, now, self.status, until=self.until)


class ManualControl:
    """Thread-safe front for the tray menu and hotkeys: each action updates the override and
    posts the result to the engine."""

    def __init__(
        self,
        post: Callable[[SourceUpdate], None],
        clock: Callable[[], datetime] = datetime.now,
    ) -> None:
        self._post = post
        self._clock = clock
        self._override = ManualOverride()
        self._lock = threading.Lock()

    def set(self, status: Status, duration: timedelta | None = None) -> None:
        with self._lock:
            update = self._override.set(status, self._clock(), duration)
        self._post(update)

    def auto(self) -> None:
        with self._lock:
            update = self._override.clear(self._clock())
        self._post(update)

    def toggle_dnd(self) -> None:
        with self._lock:
            update = self._override.toggle_dnd(self._clock())
        self._post(update)

    def current(self) -> Status | None:
        """The active override, or None in Auto (including after expiry)."""
        with self._lock:
            return self._override.status if self._override.active(self._clock()) else None
