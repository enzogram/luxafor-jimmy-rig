"""Status model and the resolver: a pure function from source updates to one status."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum

from luxstatus.config import Config, WorkHours


class Status(Enum):
    OFF = "off"  # outside work hours, screen locked, or user turned it off
    AVAILABLE = "available"
    TENTATIVE = "tentative"  # tentative calendar block
    BUSY = "busy"  # busy calendar block / OOF
    CALL = "call"  # mic (or camera) in use by a filtered app
    DND = "dnd"  # manual


class Source(Enum):
    MANUAL = "manual"
    SESSION = "session"
    DEVICES = "devices"
    OUTLOOK = "outlook"


# Outlook OlBusyStatus values.
OL_FREE, OL_TENTATIVE, OL_BUSY, OL_OOF, OL_WORKING_ELSEWHERE = range(5)

_CALENDAR_RANK = {OL_TENTATIVE: 1, OL_BUSY: 2, OL_OOF: 2}


@dataclass(frozen=True)
class CalendarBlock:
    start: datetime
    end: datetime
    busy_status: int
    subject: str = ""


@dataclass(frozen=True)
class SourceUpdate:
    """The latest report from one source. ``status=None`` means "nothing to assert".

    - manual: ``status`` is DND/AVAILABLE/OFF or None (auto); ``until`` is the expiry.
    - session: ``status`` is OFF while locked, else None.
    - devices: ``status`` is CALL or None; ``camera`` is set when the camera is on.
    - outlook: ``blocks`` holds the look-ahead window; the resolver picks what covers now.
    """

    source: Source
    ts: datetime
    status: Status | None = None
    detail: str = ""
    until: datetime | None = None
    camera: bool = False
    blocks: tuple[CalendarBlock, ...] = ()


@dataclass(frozen=True)
class Resolution:
    status: Status
    source: Source | None = None
    detail: str = ""
    camera: bool = False

    def describe(self) -> str:
        """Tooltip text, e.g. ``CALL — MSTeams (mic + camera)``."""
        name = self.status.name
        return f"{name} — {self.detail}" if self.detail else name


MANUAL_STATUSES = frozenset({Status.DND, Status.AVAILABLE, Status.OFF})


def resolve(latest: Mapping[Source, SourceUpdate], now: datetime, config: Config) -> Resolution:
    """Pick the status to show. Highest priority wins; stale sources are ignored."""
    fresh = {s: u for s, u in latest.items() if not is_stale(u, now, config)}

    manual = fresh.get(Source.MANUAL)
    if manual and manual.status in MANUAL_STATUSES and (manual.until is None or now < manual.until):
        detail = "Manual" + (f" until {manual.until:%H:%M}" if manual.until else "")
        return Resolution(manual.status, Source.MANUAL, detail)

    session = fresh.get(Source.SESSION)
    if session and session.status is Status.OFF:
        return Resolution(Status.OFF, Source.SESSION, session.detail or "Locked")

    devices = fresh.get(Source.DEVICES)
    if devices and devices.status is Status.CALL:
        return Resolution(Status.CALL, Source.DEVICES, devices.detail, devices.camera)

    # Like a call, a Busy/Tentative meeting shows at any hour: work hours only decide whether
    # an empty calendar means AVAILABLE or OFF.
    outlook = fresh.get(Source.OUTLOOK)
    if outlook:
        status, block = calendar_status(outlook.blocks, now)
        if status is not None and block is not None:
            detail = f"Outlook: {block.subject or '(no subject)'} until {block.end:%H:%M}"
            return Resolution(status, Source.OUTLOOK, detail)

    if not in_work_hours(now, config.work_hours):
        return Resolution(Status.OFF, None, "Outside work hours")

    return Resolution(Status.AVAILABLE)


def stale_after(source: Source, config: Config) -> timedelta | None:
    """How long an update stays valid; None means it never goes stale."""
    interval = {
        Source.DEVICES: config.devices.poll_seconds,
        Source.OUTLOOK: config.outlook.poll_seconds,
        Source.SESSION: config.session.poll_seconds,
    }.get(source)
    if interval is None:
        return None
    return timedelta(seconds=interval * config.resolver.stale_factor)


def is_stale(update: SourceUpdate, now: datetime, config: Config) -> bool:
    limit = stale_after(update.source, config)
    return limit is not None and now - update.ts > limit


def in_work_hours(now: datetime, wh: WorkHours) -> bool:
    if not wh.enabled:
        return True
    return now.weekday() in wh.days and wh.start <= now.time() < wh.end


def calendar_status(
    blocks: Iterable[CalendarBlock], now: datetime
) -> tuple[Status | None, CalendarBlock | None]:
    """Busiest block covering ``now``. BUSY/OOF beats TENTATIVE; free/elsewhere count as nothing."""
    best: CalendarBlock | None = None
    best_rank = 0
    for block in blocks:
        rank = _CALENDAR_RANK.get(block.busy_status, 0)
        if block.start <= now < block.end and rank > best_rank:
            best, best_rank = block, rank
    if best is None:
        return None, None
    return (Status.BUSY if best_rank == 2 else Status.TENTATIVE), best
