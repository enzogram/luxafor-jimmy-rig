from datetime import datetime, timedelta

from luxstatus.config import DevicesConfig
from luxstatus.sources.devices import (
    MICROPHONE,
    WEBCAM,
    AppEntry,
    Debouncer,
    DevicesSource,
    active_apps,
    parse_consent_tree,
)
from luxstatus.state import Source, Status

NOW = datetime(2026, 10, 21, 10, 0)
LIVE = {"LastUsedTimeStart": 134000000000000000, "LastUsedTimeStop": 0}
IDLE = {"LastUsedTimeStart": 134000000000000000, "LastUsedTimeStop": 134000000000000999}
NEVER = {"Value": "Allow"}
ZOOM = "C:#Users#me#AppData#Roaming#Zoom#bin#Zoom.exe"
CHROME = "C:#Program Files#Google#Chrome#Application#chrome.exe"
RECORDER = "C:#Tools#VoiceRecorder.exe"


def tree(packaged=None, non_packaged=None):
    t = dict(packaged or {})
    t["NonPackaged"] = dict(non_packaged or {})
    return t


def names(*n):
    return lambda: {x.lower() for x in n}


def test_parse_packaged_and_non_packaged():
    entries = parse_consent_tree(
        tree(
            {"MSTeams_8wekyb3d8bbwe": LIVE, "Microsoft.WindowsCamera_8wekyb3d8bbwe": NEVER},
            {ZOOM: IDLE},
        )
    )
    by_key = {e.key: e for e in entries}
    teams = by_key["MSTeams_8wekyb3d8bbwe"]
    assert teams.packaged and teams.in_use and teams.name == "MSTeams"
    assert not by_key["Microsoft.WindowsCamera_8wekyb3d8bbwe"].in_use
    zoom = by_key[ZOOM]
    assert not zoom.packaged and not zoom.in_use
    assert zoom.exe_path == r"C:\Users\me\AppData\Roaming\Zoom\bin\Zoom.exe"
    assert zoom.name == "Zoom.exe"


def test_in_use_needs_start_and_zero_stop():
    assert AppEntry("x", True, 5, 0).in_use
    assert not AppEntry("x", True, 0, 0).in_use
    assert not AppEntry("x", True, 5, 6).in_use


def test_filter_include_exclude_case_insensitive():
    t = tree({"MSTeams_8wekyb3d8bbwe": LIVE}, {ZOOM: LIVE, RECORDER: LIVE})
    running = names("Zoom.exe", "VoiceRecorder.exe")
    got = {e.name for e in active_apps(t, ["msteams", "ZOOM"], [], running)}
    assert got == {"MSTeams", "Zoom.exe"}
    got = {e.name for e in active_apps(t, [], ["recorder"], running)}
    assert got == {"MSTeams", "Zoom.exe"}
    got = {e.name for e in active_apps(t, [], [], running)}
    assert got == {"MSTeams", "Zoom.exe", "VoiceRecorder.exe"}


def test_crashed_non_packaged_app_is_ignored():
    t = tree(non_packaged={ZOOM: LIVE})
    assert active_apps(t, [], [], names()) == []
    assert [e.name for e in active_apps(t, [], [], names("zoom.exe"))] == ["Zoom.exe"]


def test_process_list_only_read_when_needed():
    calls = []

    def running():
        calls.append(1)
        return set()

    active_apps(tree({"MSTeams_8wekyb3d8bbwe": LIVE}, {ZOOM: IDLE}), [], [], running)
    assert calls == []


def test_debouncer_needs_consecutive_reads_to_turn_on():
    d = Debouncer(on_reads=2, off_seconds=10)
    assert not d.update(True, NOW)
    assert not d.update(False, NOW + timedelta(seconds=2))
    assert not d.update(True, NOW + timedelta(seconds=4))
    assert d.update(True, NOW + timedelta(seconds=6))


def test_debouncer_holds_through_short_gaps():
    d = Debouncer(on_reads=2, off_seconds=10)
    d.update(True, NOW)
    d.update(True, NOW)
    t = NOW
    for _ in range(4):  # 8 s of idle
        t += timedelta(seconds=2)
        assert d.update(False, t)
    t += timedelta(seconds=2)
    assert d.update(True, t)  # reconnect resets the off timer
    idle_from = t + timedelta(seconds=2)
    for _ in range(5):  # first idle read starts the timer; 8 s later still on
        t += timedelta(seconds=2)
        assert d.update(False, t)
    assert t - idle_from == timedelta(seconds=8)
    t += timedelta(seconds=2)
    assert not d.update(False, t)  # 10 s after the first idle read


class FakeRegistry:
    def __init__(self):
        self.mic = tree()
        self.cam = tree()

    def __call__(self, capability):
        return {MICROPHONE: self.mic, WEBCAM: self.cam}[capability]


def test_source_reports_call_with_detail_and_camera():
    reg = FakeRegistry()
    src = DevicesSource(DevicesConfig(), reg, names("zoom.exe"))
    assert src.poll(NOW).status is None

    reg.mic = tree({"MSTeams_8wekyb3d8bbwe": LIVE})
    reg.cam = tree({"MSTeams_8wekyb3d8bbwe": LIVE})
    assert src.poll(NOW).status is None  # first read
    u = src.poll(NOW + timedelta(seconds=2))
    assert (u.source, u.status, u.camera) == (Source.DEVICES, Status.CALL, True)
    assert u.detail == "MSTeams (mic + camera)"

    # Call ends: CALL with the last detail is held through the debounce window.
    reg.mic = reg.cam = tree()
    u = src.poll(NOW + timedelta(seconds=4))
    assert u.status is Status.CALL and u.detail == "MSTeams (mic + camera)"
    assert src.poll(NOW + timedelta(seconds=14)).status is None


def test_source_mic_only_from_filtered_desktop_app():
    reg = FakeRegistry()
    reg.mic = tree(non_packaged={ZOOM: LIVE, CHROME: IDLE})
    cfg = DevicesConfig(on_debounce_reads=1)
    u = DevicesSource(cfg, reg, names("zoom.exe")).poll(NOW)
    assert (u.status, u.detail, u.camera) == (Status.CALL, "Zoom.exe (mic)", False)
