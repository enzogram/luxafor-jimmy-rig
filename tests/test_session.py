from datetime import datetime

from luxstatus.config import Config
from luxstatus.sources.session import SessionSource
from luxstatus.state import Source, SourceUpdate, Status, resolve

NOW = datetime(2026, 10, 21, 10, 0)


def test_locked_reports_off_unlocked_reports_nothing():
    locked = [True]
    src = SessionSource(is_locked=lambda: locked[0])
    u = src.poll(NOW)
    assert (u.source, u.status, u.detail) == (Source.SESSION, Status.OFF, "Locked")
    locked[0] = False
    assert src.poll(NOW).status is None


def test_lock_turns_off_light_during_a_call():
    latest = {
        Source.SESSION: SessionSource(is_locked=lambda: True).poll(NOW),
        Source.DEVICES: SourceUpdate(Source.DEVICES, NOW, Status.CALL, "MSTeams (mic)"),
    }
    r = resolve(latest, NOW, Config())
    assert r.status is Status.OFF and r.describe() == "OFF — Locked"
