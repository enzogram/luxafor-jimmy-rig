from datetime import datetime, timedelta

import pytest

from luxstatus.config import Config
from luxstatus.state import (
    OL_BUSY,
    OL_FREE,
    OL_OOF,
    OL_TENTATIVE,
    OL_WORKING_ELSEWHERE,
    CalendarBlock,
    Resolution,
    Source,
    SourceUpdate,
    Status,
    calendar_status,
    resolve,
)

CFG = Config()
NOW = datetime(2026, 10, 21, 10, 0)  # Wednesday, inside default work hours
EVENING = datetime(2026, 10, 21, 19, 0)
SATURDAY = datetime(2026, 10, 24, 10, 0)


def upd(source, status=None, ts=NOW, **kw):
    return SourceUpdate(source, ts, status, **kw)


def block(busy, start=NOW - timedelta(minutes=5), end=NOW + timedelta(minutes=25), subject="Sync"):
    return CalendarBlock(start, end, busy, subject)


def outlook(*blocks, ts=NOW):
    return upd(Source.OUTLOOK, ts=ts, blocks=tuple(blocks))


def status_of(*updates, now=NOW):
    return resolve({u.source: u for u in updates}, now, CFG).status


def test_nothing_reported_is_available():
    assert resolve({}, NOW, CFG) == Resolution(Status.AVAILABLE)


def test_outside_work_hours_is_off():
    assert status_of(now=EVENING) is Status.OFF
    assert status_of(now=SATURDAY) is Status.OFF
    assert (
        status_of(
            outlook(block(OL_BUSY, start=EVENING, end=EVENING + timedelta(hours=1))), now=EVENING
        )
        is Status.OFF
    )


@pytest.mark.parametrize("status", [Status.DND, Status.AVAILABLE, Status.OFF])
def test_manual_beats_everything(status):
    locked = upd(Source.SESSION, Status.OFF)
    call = upd(Source.DEVICES, Status.CALL)
    r = resolve(
        {u.source: u for u in [upd(Source.MANUAL, status), locked, call, outlook(block(OL_BUSY))]},
        NOW,
        CFG,
    )
    assert r.status is status
    assert r.source is Source.MANUAL


def test_manual_applies_outside_work_hours():
    assert status_of(upd(Source.MANUAL, Status.DND, ts=EVENING), now=EVENING) is Status.DND


def test_manual_expiry():
    m = upd(Source.MANUAL, Status.DND, until=NOW + timedelta(minutes=30))
    assert status_of(m) is Status.DND
    assert resolve({Source.MANUAL: m}, NOW, CFG).detail == "Manual until 10:30"
    assert status_of(m, now=NOW + timedelta(minutes=30)) is Status.AVAILABLE


def test_manual_never_goes_stale():
    m = upd(Source.MANUAL, Status.DND, ts=NOW - timedelta(days=2))
    assert status_of(m) is Status.DND


def test_manual_auto_falls_through():
    assert status_of(upd(Source.MANUAL, None), upd(Source.DEVICES, Status.CALL)) is Status.CALL


def test_lock_beats_call():
    assert (
        status_of(upd(Source.SESSION, Status.OFF), upd(Source.DEVICES, Status.CALL)) is Status.OFF
    )


def test_call_beats_calendar_and_work_hours():
    assert status_of(upd(Source.DEVICES, Status.CALL), outlook(block(OL_TENTATIVE))) is Status.CALL
    evening_call = upd(Source.DEVICES, Status.CALL, ts=EVENING, detail="Zoom.exe (mic)")
    r = resolve({Source.DEVICES: evening_call}, EVENING, CFG)
    assert r.status is Status.CALL
    assert r.describe() == "CALL — Zoom.exe (mic)"


def test_call_carries_camera_flag():
    r = resolve({Source.DEVICES: upd(Source.DEVICES, Status.CALL, camera=True)}, NOW, CFG)
    assert r.camera


@pytest.mark.parametrize(
    ("busy", "expected"),
    [
        (OL_BUSY, Status.BUSY),
        (OL_OOF, Status.BUSY),
        (OL_TENTATIVE, Status.TENTATIVE),
        (OL_WORKING_ELSEWHERE, Status.AVAILABLE),
        (OL_FREE, Status.AVAILABLE),
    ],
)
def test_calendar_busy_status(busy, expected):
    assert status_of(outlook(block(busy))) is expected


def test_calendar_detail():
    r = resolve({Source.OUTLOOK: outlook(block(OL_BUSY, subject="Weekly TSF sync"))}, NOW, CFG)
    assert r.describe() == "BUSY — Outlook: Weekly TSF sync until 10:25"


def test_calendar_busiest_overlapping_block_wins():
    s, b = calendar_status([block(OL_TENTATIVE, subject="t"), block(OL_BUSY, subject="b")], NOW)
    assert (s, b.subject) == (Status.BUSY, "b")


def test_calendar_block_boundaries():
    b = block(OL_BUSY, start=NOW, end=NOW + timedelta(minutes=30))
    assert calendar_status([b], NOW - timedelta(seconds=1)) == (None, None)
    assert calendar_status([b], NOW)[0] is Status.BUSY
    assert calendar_status([b], NOW + timedelta(minutes=30)) == (None, None)


def test_lookahead_block_applies_when_it_starts_without_a_new_poll():
    starts = NOW + timedelta(seconds=60)
    ol = outlook(block(OL_BUSY, start=starts, end=starts + timedelta(hours=1)))
    assert status_of(ol) is Status.AVAILABLE
    assert status_of(ol, now=starts) is Status.BUSY


def test_stale_sources_are_ignored():
    # devices: 2 s poll x 3 = 6 s
    call = upd(Source.DEVICES, Status.CALL, ts=NOW - timedelta(seconds=6))
    assert status_of(call) is Status.CALL
    call = upd(Source.DEVICES, Status.CALL, ts=NOW - timedelta(seconds=7))
    assert status_of(call) is Status.AVAILABLE
    # outlook: 30 s x 3 = 90 s
    assert status_of(outlook(block(OL_BUSY), ts=NOW - timedelta(seconds=91))) is Status.AVAILABLE
    # session: a crashed lock poller must not keep the light off
    assert (
        status_of(upd(Source.SESSION, Status.OFF, ts=NOW - timedelta(seconds=7)))
        is Status.AVAILABLE
    )


def test_work_hours_disabled():
    from dataclasses import replace

    cfg = replace(CFG, work_hours=replace(CFG.work_hours, enabled=False))
    assert resolve({}, EVENING, cfg).status is Status.AVAILABLE
