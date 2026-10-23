"""Smoke tests for the real Win32 code paths; they run on the Windows CI job only."""

import sys
import time

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Win32 only")


def test_hotkey_thread_with_nothing_registered_stops_promptly():
    from luxstatus.hotkeys import HotkeyThread

    thread = HotkeyThread({})
    thread.start()
    started = time.monotonic()
    thread.stop()
    assert not thread.is_alive()
    assert time.monotonic() - started < 1


def test_workstation_locked_returns_a_bool():
    from luxstatus.sources.session import workstation_locked

    assert isinstance(workstation_locked(), bool)


def test_outlook_thread_with_real_com_pumps_and_stops_promptly():
    from luxstatus.config import OutlookConfig
    from luxstatus.sources.outlook import OutlookThread

    class Client:
        def fetch(self, start, end):
            return []

        def reset(self):
            pass

    posted = []
    thread = OutlookThread(OutlookConfig(poll_seconds=60), posted.append, client=Client())
    thread.start()
    deadline = time.monotonic() + 5
    while not posted and time.monotonic() < deadline:
        time.sleep(0.01)
    started = time.monotonic()
    thread.stop()
    assert posted and not thread.is_alive()
    assert time.monotonic() - started < 1
