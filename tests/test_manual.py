from datetime import datetime, timedelta

import pytest

from luxstatus.sources.manual import ManualOverride
from luxstatus.state import Source, Status

NOW = datetime(2026, 10, 21, 10, 0)


def test_set_with_duration():
    m = ManualOverride()
    u = m.set(Status.DND, NOW, timedelta(hours=1))
    assert (u.source, u.status, u.until) == (Source.MANUAL, Status.DND, NOW + timedelta(hours=1))
    assert m.active(NOW)
    assert not m.active(NOW + timedelta(hours=1))


def test_clear_returns_to_auto():
    m = ManualOverride()
    m.set(Status.OFF, NOW)
    u = m.clear(NOW)
    assert u.status is None and u.until is None
    assert not m.active(NOW)


def test_toggle_dnd():
    m = ManualOverride()
    assert m.toggle_dnd(NOW).status is Status.DND
    assert m.toggle_dnd(NOW).status is None
    m.set(Status.AVAILABLE, NOW)
    assert m.toggle_dnd(NOW).status is Status.DND


def test_toggle_after_expired_dnd_sets_dnd_again():
    m = ManualOverride()
    m.set(Status.DND, NOW, timedelta(minutes=30))
    later = NOW + timedelta(hours=1)
    u = m.toggle_dnd(later)
    assert u.status is Status.DND and u.until is None


@pytest.mark.parametrize("status", [Status.CALL, Status.BUSY, Status.TENTATIVE])
def test_rejects_non_manual_statuses(status):
    with pytest.raises(ValueError):
        ManualOverride().set(status, NOW)
