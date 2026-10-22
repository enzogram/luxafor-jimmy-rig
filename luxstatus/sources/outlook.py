"""Outlook calendar via classic Outlook's COM object model.

``to_blocks`` and ``restrict_filter`` are pure. ``OutlookClient`` holds COM objects and must
be created, used and dropped on one thread that has called ``pythoncom.CoInitialize()``.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from luxstatus.state import OL_FREE, CalendarBlock

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
    """Items overlapping [start, end). Outlook parses these dates using the Windows locale;
    the probe confirms the format on the real machine."""
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

    def fetch(self, start: datetime, end: datetime) -> list[CalendarItem] | None:
        """Items overlapping [start, end), or None when Outlook is not running."""
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
