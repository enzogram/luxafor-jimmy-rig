import asyncio
import threading
import time
from dataclasses import replace
from datetime import datetime

from luxstatus.config import Config, DevicesConfig
from luxstatus.engine import Engine
from luxstatus.main import App, Poller, default_pollers, poll_loop, run
from luxstatus.sources.devices import MICROPHONE, WEBCAM, DevicesSource
from luxstatus.state import Source, SourceUpdate, Status
from tests.fakes import FakeLight

LIVE = {"LastUsedTimeStart": 134000000000000000, "LastUsedTimeStop": 0}


def test_default_pollers_respect_enabled():
    assert [p.name for p in default_pollers(Config())] == ["devices", "session"]
    off = replace(
        Config(),
        devices=replace(Config().devices, enabled=False),
        session=replace(Config().session, enabled=False),
    )
    assert default_pollers(off) == []


def test_poll_loop_survives_errors_and_stops():
    calls = []

    def poll(now):
        calls.append(now)
        if len(calls) == 2:
            raise OSError("registry hiccup")
        return SourceUpdate(Source.DEVICES, now)

    async def main():
        queue = asyncio.Queue()
        stop = asyncio.Event()
        task = asyncio.create_task(poll_loop(Poller("t", poll, 0.01), queue, stop))
        while len(calls) < 4:
            await asyncio.sleep(0.01)
        stop.set()
        await asyncio.wait_for(task, 1)
        return queue.qsize()

    delivered = asyncio.run(main())
    assert delivered == len(calls) - 1  # the failing poll delivers nothing


def test_light_follows_a_call_end_to_end():
    """Fake registry → DevicesSource → poll loop → engine → light."""
    registry = {MICROPHONE: {}, WEBCAM: {}}
    cfg = replace(Config(), work_hours=replace(Config().work_hours, enabled=False))
    devices = DevicesSource(
        DevicesConfig(on_debounce_reads=1, off_debounce_seconds=0),
        read_tree=lambda cap: registry[cap],
        running_names=set,
    )
    light = FakeLight()
    seen = []

    async def main():
        stop = asyncio.Event()

        def on_change(r):
            seen.append(r.status)

        engine = Engine(cfg, light, on_change=on_change)
        task = asyncio.create_task(run(engine, stop, [Poller("devices", devices.poll, 0.01)]))

        async def until(status):
            for _ in range(200):
                if light.shown and light.shown[-1].status is status:
                    return
                await asyncio.sleep(0.01)
            raise AssertionError(f"light never showed {status}")

        await until(Status.AVAILABLE)
        registry[MICROPHONE] = {"MSTeams_8wekyb3d8bbwe": LIVE}
        await until(Status.CALL)
        registry[MICROPHONE] = {}
        await until(Status.AVAILABLE)
        stop.set()
        await asyncio.wait_for(task, 1)

    asyncio.run(main())
    assert [r.status for r in light.shown] == [Status.AVAILABLE, Status.CALL, Status.AVAILABLE]
    assert light.shown[1].detail == "MSTeams (mic)"


def wait_for(predicate, timeout=2.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition not met")


def test_app_threads_manual_override_from_other_threads_and_shuts_down():
    light = FakeLight()
    app = App(Config(), light, [])
    seen = []
    app.listeners.append(lambda r: seen.append(r.status))
    app.listeners.append(lambda r: 1 / 0)  # a broken listener must not stop the engine
    app.manual.set(Status.DND)  # posted before the loop starts: must not be lost
    app.start()
    wait_for(lambda: light.shown and light.shown[-1].status is Status.DND)

    t = threading.Thread(target=app.manual.toggle_dnd)  # e.g. the hotkey thread
    t.start()
    t.join()
    wait_for(lambda: app.manual.current() is None and len(seen) >= 2)

    app.shutdown()
    assert not app.thread.is_alive()
    assert light.shown[-1].status is Status.OFF
    assert Status.DND in seen


def test_outlook_thread_drives_light_through_app():
    from datetime import datetime, timedelta

    from luxstatus.config import OutlookConfig
    from luxstatus.sources.outlook import CalendarItem, OutlookThread
    from luxstatus.state import OL_BUSY

    now = datetime.now()
    meeting = CalendarItem(now - timedelta(minutes=5), now + timedelta(minutes=30), OL_BUSY, "Sync")

    class Client:
        def fetch(self, start, end):
            return [meeting]

        def reset(self):
            pass

    cfg = replace(Config(), work_hours=replace(Config().work_hours, enabled=False))
    light = FakeLight()
    app = App(cfg, light, [])
    outlook = OutlookThread(OutlookConfig(poll_seconds=60), app.post, client=Client(), com=False)
    app.start()
    outlook.start()
    try:
        wait_for(lambda: light.shown and light.shown[-1].status is Status.BUSY)
        assert light.shown[-1].detail.startswith("Outlook: Sync until ")
    finally:
        outlook.stop()
        app.shutdown()


def test_app_reconfigure_rewrites_light_rebuilds_pollers_and_drops_disabled_sources():
    light = FakeLight()
    built = []

    def make_pollers(cfg):
        built.append(cfg)
        return []

    app = App(Config(), light, [], make_pollers=make_pollers)
    app.post(SourceUpdate(Source.DEVICES, datetime.now(), Status.CALL, "MSTeams (mic)"))
    app.start()
    try:
        wait_for(lambda: light.shown and light.shown[-1].status is Status.CALL)
        shown = len(light.shown)
        new = replace(Config(), devices=replace(Config().devices, enabled=False))
        app.reconfigure(new)
        wait_for(lambda: built == [new])
        assert app.engine.config is new
        assert Source.DEVICES not in app.engine.latest  # the call no longer counts
        assert len(light.shown) > shown  # light rewritten after reload
        assert light.shown[-1].status is not Status.CALL
    finally:
        app.shutdown()


def test_source_pollers_keep_debounce_state_across_reload():
    from luxstatus.main import SourcePollers

    pollers = SourcePollers()
    pollers(Config())
    devices = pollers.devices
    devices.debouncer.state = True  # mid-call
    cfg = replace(Config(), devices=replace(Config().devices, on_debounce_reads=5))
    [p, _] = pollers(cfg)
    assert pollers.devices is devices and devices.debouncer.state
    assert devices.debouncer.on_reads == 5
    assert p.interval == cfg.devices.poll_seconds


def test_runtime_apply_updates_app_and_tray():
    from luxstatus.main import Runtime

    class FakeApp:
        manual = None
        post = None

        def __init__(self):
            self.configs = []

        def reconfigure(self, cfg):
            self.configs.append(cfg)

    class FakeTray:
        config = None

    app, tray = FakeApp(), FakeTray()
    rt = Runtime(Config(), app, tray)
    new = replace(Config(), colours=replace(Config().colours, busy=(1, 2, 3)))
    rt.apply(new)
    assert app.configs == [new] and tray.config is new and rt.cfg is new
    rt.stop()


def test_ensure_config_file(tmp_path):
    from luxstatus.config import template_path
    from luxstatus.main import ensure_config_file

    existing = tmp_path / "mine.toml"
    existing.write_text("")
    assert ensure_config_file(existing, str(tmp_path), template_path()) == existing

    created = ensure_config_file(None, str(tmp_path / "AppData"), template_path())
    assert created == tmp_path / "AppData" / "luxstatus" / "config.toml"
    assert created.read_text() == template_path().read_text()
    created.write_text("# edited")
    assert ensure_config_file(None, str(tmp_path / "AppData"), template_path()).read_text() == (
        "# edited"
    )  # never overwrites
