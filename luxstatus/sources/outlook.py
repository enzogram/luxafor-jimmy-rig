"""Outlook calendar via classic Outlook's COM object model.

``to_blocks`` and ``restrict_filter`` are pure. ``OutlookClient`` holds COM objects and must
be created, used and dropped on one thread that has called ``pythoncom.CoInitialize()``;
``OutlookThread`` is that thread.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol

from luxstatus.config import OutlookConfig
from luxstatus.state import OL_FREE, CalendarBlock, Source, SourceUpdate

log = logging.getLogger(__name__)

OL_FOLDER_CALENDAR = 9
# OlMeetingStatus: olMeetingCanceled, olMeetingReceivedAndCanceled.
CANCELLED_MEETING_STATUSES = frozenset({5, 7})


@dataclass(frozen=True)
class CalendarItem:
    start: datetime
    end: datetime
    busy_status: int
    subject: str = ""
    all_day: bool = False
    meeting_status: int = 0


def to_blocks(items: Iterable[CalendarItem]) -> tuple[CalendarBlock, ...]:
    """Keep items that can affect the light. Free items (all-day or not) and cancelled
    meetings never do."""
    return tuple(
        CalendarBlock(i.start, i.end, i.busy_status, i.subject)
        for i in items
        if i.busy_status != OL_FREE
        and i.meeting_status not in CANCELLED_MEETING_STATUSES
        and i.end > i.start
    )


def restrict_filter(start: datetime, end: datetime) -> str:
    """Items overlapping [start, end).

    Dates are always written MM/DD/YYYY hh:mm AM/PM. On the target machine (English US,
    custom short date dd-MMM-yy) Outlook parses this correctly, while building the string from
    the Windows short-date format found nothing: GetDateFormat via pywintypes.Time also shifted
    the time by the UTC offset. Keep this unless the probe shows otherwise on a new machine.
    """
    fmt = "%m/%d/%Y %I:%M %p"
    return f"[Start] < '{end.strftime(fmt)}' AND [End] > '{start.strftime(fmt)}'"


def com_datetime(value: Any) -> datetime:
    """pywin32 tags appointment times as UTC although they are local wall-clock times."""
    return datetime(value.year, value.month, value.day, value.hour, value.minute, value.second)


def outlook_running() -> bool:
    import psutil

    return any(
        (p.info.get("name") or "").lower() == "outlook.exe" for p in psutil.process_iter(["name"])
    )


class OutlookClient:
    """Connects to an already-running Outlook only. Call ``reset()`` after any COM error."""

    def __init__(self) -> None:
        self._calendar: Any = None

    def reset(self) -> None:
        self._calendar = None

    def folder_path(self) -> str:
        """Which calendar is being read (after a fetch); for the probe."""
        return str(self._calendar.FolderPath) if self._calendar is not None else "?"

    def fetch(
        self, start: datetime, end: datetime, raw: list[Any] | None = None
    ) -> list[CalendarItem] | None:
        """Items overlapping [start, end), or None when Outlook is not running. ``raw``
        collects the COM items, for the probe."""
        if not outlook_running():
            self.reset()
            return None
        if self._calendar is None:
            import win32com.client

            app = win32com.client.Dispatch("Outlook.Application")
            self._calendar = app.GetNamespace("MAPI").GetDefaultFolder(OL_FOLDER_CALENDAR)
        items = self._calendar.Items
        items.IncludeRecurrences = True  # must be set before Sort
        items.Sort("[Start]")
        restricted = items.Restrict(restrict_filter(start, end))
        # With recurrences, Count is meaningless; only GetFirst/GetNext work.
        result = []
        item = restricted.GetFirst()
        while item is not None:
            if raw is not None:
                raw.append(item)
            result.append(
                CalendarItem(
                    start=com_datetime(item.Start),
                    end=com_datetime(item.End),
                    busy_status=int(item.BusyStatus),
                    subject=str(item.Subject or ""),
                    all_day=bool(item.AllDayEvent),
                    meeting_status=int(item.MeetingStatus),
                )
            )
            item = restricted.GetNext()
        return result


class CalendarClient(Protocol):
    def fetch(self, start: datetime, end: datetime) -> list[CalendarItem] | None: ...
    def reset(self) -> None: ...


class OutlookThread(threading.Thread):
    """Polls the calendar on its own thread and posts the look-ahead blocks.

    When Outlook is not running nothing is posted, so the last report goes stale and the
    resolver treats the calendar as unknown. A COM error (Outlook restarted, sleep/resume)
    drops the cached objects and retries once with a fresh connection.
    """

    def __init__(
        self,
        config: OutlookConfig,
        post: Callable[[SourceUpdate], None],
        client: CalendarClient | None = None,
        clock: Callable[[], datetime] = datetime.now,
        com: bool = True,
    ) -> None:
        super().__init__(name="outlook", daemon=True)
        self.config = config
        self.post = post
        self.client = client or OutlookClient()
        self.clock = clock
        self.com = com
        self._stop_event = threading.Event()
        self._failing = False
        self._running: bool | None = None

    def run(self) -> None:
        if self.com:
            import pythoncom

            pythoncom.CoInitialize()
        try:
            while not self._stop_event.is_set():
                self.poll_once()
                self._idle(self.config.poll_seconds)
        finally:
            self.client.reset()  # release COM objects on this thread
            if self.com:
                pythoncom.CoUninitialize()

    def _idle(self, seconds: float) -> None:
        """Wait between polls. A COM thread (STA) owns a hidden window and must keep pumping
        messages, or programs that broadcast with SendMessage can hang waiting for it."""
        if not self.com:
            self._stop_event.wait(seconds)
            return
        import pythoncom

        deadline = time.monotonic() + seconds
        while not self._stop_event.is_set():
            pythoncom.PumpWaitingMessages()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            self._stop_event.wait(min(0.2, remaining))

    def stop(self, timeout: float = 5) -> None:
        self._stop_event.set()
        if self.is_alive():
            self.join(timeout)

    def poll_once(self) -> SourceUpdate | None:
        now = self.clock()
        end = now + timedelta(minutes=self.config.lookahead_minutes)
        items = None
        for attempt in (1, 2):
            try:
                items = self.client.fetch(now, end)
                break
            except Exception:
                self.client.reset()
                if attempt == 2:
                    if not self._failing:
                        log.exception("Outlook read failed; will keep retrying")
                    self._failing = True
                    return None
        if self._failing:
            log.info("Outlook read recovered")
            self._failing = False

        running = items is not None
        if running != self._running:
            log.info("Outlook %s", "connected" if running else "not running; calendar unknown")
            self._running = running
        if items is None:
            return None
        update = SourceUpdate(Source.OUTLOOK, now, blocks=to_blocks(items))
        self.post(update)
        return update
